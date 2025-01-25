#https://discord.com/oauth2/authorize?client_id=1323662026861449237&permissions=8&integration_type=0&scope=bot+applications.commands

import discord
from discord import PCMVolumeTransformer
from discord.ext import commands
from discord.ui import View, Select
import os # default module
from dotenv import load_dotenv
# from googleapiclient.discovery import build
import yt_dlp
import asyncio
import atexit
import hashlib
import random
import time
from filelock import FileLock
import traceback

load_dotenv() # load all the variables from the env file
bot = discord.Bot()

# youtube = build('youtube', 'v3', developerKey=os.getenv('YOUTUBE_API_KEY'))

def generate_unique_filename(url):
    hashed_url = hashlib.md5(url.encode()).hexdigest()
    return f"{hashed_url}"

class URLUserStorage():
    def __init__(self):
        self.data = []
        self.isRotate = "noRotate"
        
    def add_entry(self, title, url, user):
        self.data.append({"title": title, "url": url, "user": user})
    
    def get_all_entries(self):
        return self.data
    
    def get_current_data(self):
        return self.data[0]

    def get_next_data(self):
        if len(self.data) <= 1:
            return {"title": "없음", "url": "없음", "user": "없음"}
        else:
            return self.data[1]

    def clear_entries(self):
        self.data = []

    def move_elements(self):
        if self.data and not self.isRotate == "RepeatOne":
            element = self.data.pop(0)

            if self.isRotate == "Rotate":
                self.data.append(element)

    def shuffle(self):
        random.seed(int(time.time() * 1000))
        if len(self.data) > 1:
            first_element = self.data[0]  # 첫 번째 요소 저장
            rest = self.data[1:]  # 나머지 요소 분리
            random.shuffle(rest)  # 나머지만 섞기
            self.data = [first_element] + rest  # 다시 합치기

class CustomFFmpegPCMAudio(discord.FFmpegPCMAudio):
    def __init__(self, source, *, before_options=None, options=None):
        super().__init__(source, before_options=before_options, options=options)
        self.start_time = time.time()

    @property
    def elapsed_time(self):
        # 현재 경과 시간 계산
        return time.time() - self.start_time

    def reset_elapsed_time(self):
        # 경과 시간 초기화
        self.start_time = time.time()

class NowPlayManager(discord.ui.View):
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        super().__init__()
        self.playStorage = URLUserStorage()
        self.ctx = None
        self.current_target_mp3 = None
        self.tasks = []
        self.active_files = set()
        
        self.isEmotion = False
        self.isPause = False
        self.isPlay = False
        self.isScreen = False
        self.isReservation = False
        self.isNext = False

        self.elapsed_time = 0
        self.np_volume = 1.0
        self.atempo = 1.0

        self.pause_interaction = None

        self.message = None

        self.ffmpeg_options = {
            'options': '-vn -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
        }

    async def show_emotion(self, ctx, text, delay):
        defer_flag = False
        while self.isEmotion:
            if not defer_flag:
                print("defer")
                await ctx.defer()
                defer_flag = True
            await asyncio.sleep(0.1)

        self.isEmotion = True

        if defer_flag:
            message = await ctx.send(text)
        else:
            message = await ctx.respond(text, ephemeral=True)

        if delay:
            await asyncio.sleep(delay)
            await self.delete_exception(ctx)
            self.isEmotion = False
        
        return message
    
    async def edit_emotion(self, ctx, emotion_message, text, delay):
        await emotion_message.edit(content=text)
        if delay:
            await asyncio.sleep(delay)
            await self.delete_exception(ctx)
            self.isEmotion = False
    
    async def delete_emotion(self, ctx):
        await self.delete_exception(ctx)
        self.isEmotion = False

    async def delete_exception(self, ctx):
        try:
            await ctx.delete()
        except discord.NotFound:
            print("삭제할 메시지가 없습니다.")
        except discord.Forbidden:
            print("메시지 삭제 권한이 없습니다.")
        except Exception as e:
            print(f"예상치 못한 오류 발생: {e}")

    async def download_audio_from_urls(self, data):
        semaphore = asyncio.Semaphore(1)  # 동시에 실행할 다운로드 수 제한
        loop = asyncio.get_event_loop()

        async def download_audio(url, target):
            temp_target = target + ".temp"  # 임시 파일 이름
            lock = FileLock(target + ".lock")  # 파일 잠금 생성

            with lock:  # 파일 잠금 사용
                ytdl_format_options = {
                    'format': 'bestaudio/best',
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '320',
                    }],
                    'quiet': True,
                    'noplaylist': True,
                    'outtmpl': temp_target,  # 임시 파일 이름
                    'ratelimit': 2 * 1024 * 1024,
                }
                ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

                # 동기 작업을 비동기적으로 실행
                await loop.run_in_executor(None, ytdl.download, [url])

                # 변환 완료 후 임시 파일 이동
                if os.path.exists(temp_target + ".mp3"):
                    os.replace(temp_target + ".mp3", target + ".mp3")
                print(f"Downloaded and saved: {target}.mp3")

        async def process_entry(entry):
            url = entry['url']
            target_mp3 = generate_unique_filename(url)

            if target_mp3 in self.active_files:
                print(f"Skipping {target_mp3}, already in progress.")
                return  # 중복 작업 방지
            
            self.active_files.add(target_mp3)  # 작업 중인 파일 추가

            try:
                if not os.path.exists(target_mp3 + ".mp3"):
                    async with semaphore:
                        await download_audio(url, target_mp3)
            finally:
                self.active_files.remove(target_mp3)  # 작업 완료 후 제거

        # 모든 데이터에 대해 태스크 생성
        self.tasks = [asyncio.create_task(process_entry(entry)) for entry in data]
        await asyncio.gather(*self.tasks)  # 태스크 병렬 실행

    async def start_downloads(self, data):
        await self.download_audio_from_urls(data)

    async def cancel_downloads(self):
        print("다운로드 작업 취소 중...")
        for task in self.tasks:
            if not task.done():  # 완료되지 않은 태스크만 취소
                task.cancel()
        self.tasks = []  # 태스크 리스트 초기화
        print("모든 다운로드 작업이 취소되었습니다.")

    def  get_youtube_title(self, url):
        ytdl_options = {
        'quiet': True,  # 출력 최소화
        }
        
        # yt_dlp 객체 생성
        with yt_dlp.YoutubeDL(ytdl_options) as ytdl:
            # URL에서 정보 추출
            info = ytdl.extract_info(url, download=False)  # 다운로드하지 않고 정보만 추출
            return info.get('title', None)  # 제목 가져오기

    async def add_playlist(self, url, user):
        
        self.playStorage.add_entry(self.get_youtube_title(url), url, user)
        asyncio.create_task(self.start_downloads(self.playStorage.get_all_entries()))

    async def delete_screen(self):
        if self.message is not None:
            try:
                await self.message.delete()
                self.message = None  # 삭제 후 None으로 설정
            except discord.NotFound:
                print("Message already deleted.")
            except Exception as e:
                print(f"Error deleting message: {e}")
        else:
            print("No message to delete.")
        self.isScreen = False

    async def delete_screen_file(self):
        await self.delete_screen()
        await self.delete_all_mp3_files()

    async def delete_mp3_files(self, file):
        print(f"delete: {file}")
        if os.path.exists(file):  # 파일이 존재하는지 확인
            os.remove(file)

    async def delete_all_mp3_files(self):
        folder_path = "./"
        try:
            for file_name in os.listdir(folder_path):
                if file_name.endswith(".mp3"):
                    file_path = os.path.join(folder_path, file_name)
                    os.remove(file_path)  # 파일 삭제
        except Exception as e:
            print(f"Error while deleting files: {e}")

    async def next_play(self):
        try:
            self.isNext = True
            print("next play")
            # 현재 재생할 데이터 가져오기
            data = self.playStorage.get_current_data()
            if not data:
                self.isPlay = False
                print("No more entries to play.")
                return
            
            if self.ctx.voice_client and self.ctx.voice_client.is_playing():
                self.ctx.voice_client.stop()  # 현재 스트림 정지
                print("Current playback stopped.")

            url = data["url"]
            target_mp3 = generate_unique_filename(url) + ".mp3"

            # 파일이 준비될 때까지 대기
            while not os.path.exists(target_mp3):
                await asyncio.sleep(0.5)

            current_data = self.playStorage.get_current_data()
            next_data = self.playStorage.get_next_data()
            print(
                f"[요청자]: {current_data["user"]}\n[다음곡]: {next_data["title"]}\n[url]: {current_data["url"]}")
            
            await self.message.delete()

            self.message = await self.ctx.send(
                f"[요청자]: {current_data["user"]}\n[다음곡]: {next_data["title"]}\n[url]: {current_data["url"]}", view=self)

            # FFmpeg 소스 생성
            source = CustomFFmpegPCMAudio(target_mp3, options=self.ffmpeg_options)
            transformed_source = PCMVolumeTransformer(source)
            transformed_source.volume = float(self.np_volume)
            self.isNext = False

            # 재생 시작
            self.ctx.voice_client.play(
                transformed_source,
                after=self.after_playing  # 재생 완료 후 호출
            )
            await self.update_pause_button()
        except Exception as e:
            await self.ctx.send("오류가 발생했습니다.")

            error_trace = traceback.format_exc()
            print("에러 발생 위치:")
            print(error_trace)

    def after_playing(self, error):
        if error:
            print(f"Error during playback: {error}")
        else:
            if not self.isNext:
                self.playStorage.move_elements()
                if self.playStorage.get_all_entries():
                    asyncio.run_coroutine_threadsafe(self.next_play(), bot.loop)
                else:
                    print("Playback finished successfully.")
                    self.isPlay = False

        # future = asyncio.run_coroutine_threadsafe(self.delete_mp3_files(self.current_target_mp3), bot.loop)
        # future.result()

    
    async def play(self, ctx, url):
        self.ctx = ctx

        if self.playStorage.get_all_entries():
            while self.isReservation:
                await asyncio.sleep(0.1)
            self.isReservation = True
            message_nothing = await self.show_emotion(ctx, f"...", 0)
            await self.edit_emotion(ctx, message_nothing, f"노래 예약\n[제목]: {self.get_youtube_title(url)}\n[url]: {url}", 0)

            user_name = ctx.author.display_name

            await self.add_playlist(url, user_name)

            current_data = self.playStorage.get_current_data()
            next_data = self.playStorage.get_next_data()
            await self.message.delete()
            self.message = await self.ctx.send(
                f"[요청자]: {current_data["user"]}\n[다음곡]: {next_data["title"]}\n[url]: {current_data["url"]}", view=self)
            await self.delete_emotion(ctx)
            self.isReservation = False
            
        else:
            if self.isScreen:
                await self.delete_screen()

            try:
                await self.show_emotion(self.ctx, ":thinking:", 0)

                user = ctx.author
                
                await self.add_playlist(url, user.display_name)
                target_mp3 = generate_unique_filename(url) + ".mp3"

                while not os.path.exists(target_mp3):
                    await asyncio.sleep(0.5)

                await self.delete_emotion(self.ctx)

                source = CustomFFmpegPCMAudio(target_mp3, options=self.ffmpeg_options)
                source.reset_elapsed_time()

                transformed_source = PCMVolumeTransformer(source)
                transformed_source.volume = float(self.np_volume)

                self.ctx.voice_client.play(
                    transformed_source,
                    after=self.after_playing
                )

                self.current_target_mp3 = target_mp3
                self.isPlay = True
                self.isScreen = True
                self.isPause = False
                current_data = self.playStorage.get_current_data()
                next_data = self.playStorage.get_next_data()
                self.message = await self.ctx.send(
                    f"[요청자]: {current_data["user"]}\n[다음곡]: {next_data["title"]}\n[url]: {current_data["url"]}", view=self)
                
            except Exception as e:
                await self.ctx.send("오류가 발생했습니다.")

                error_trace = traceback.format_exc()
                print("에러 발생 위치:")
                print(error_trace)

    async def stop(self):
        self.ctx.voice_client.stop()
    
    async def list_show(self):
        return self.playStorage.get_all_entries()
    
    async def update_pause_button(self):
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.label in ["▶️ 재생"]:
                child.label = "⏸️ 중지"
                child.style = discord.ButtonStyle.secondary
                self.isPause = False

    @discord.ui.button(label="➡️ 반복 없음", style=discord.ButtonStyle.secondary, row=0)
    async def rotate_button(self, button, interaction):
        if not self.ctx.voice_client:
            return
        
        if self.playStorage.isRotate == "RepeatOne":
            button.label = "🔁 전체 반복"
            button.style=discord.ButtonStyle.success
            self.playStorage.isRotate = "Rotate"
        elif self.playStorage.isRotate == "Rotate":
            button.label = "➡️ 반복 없음"
            button.style=discord.ButtonStyle.secondary
            self.playStorage.isRotate = "noRotate"
        else:
            button.label = "🔄 한곡 반복"
            button.style=discord.ButtonStyle.primary
            self.playStorage.isRotate = "RepeatOne"

        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="🔀 셔플", style=discord.ButtonStyle.secondary, row=0)
    async def shuffle_button(self, button, interaction):
        if not self.ctx.voice_client:
            return
        
        button.disabled = True
        
        self.playStorage.shuffle()
        
        button.disabled = False

        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="⏸️ 중지", style=discord.ButtonStyle.secondary, row=0)
    async def pause_button(self, button, interaction):
        if not self.ctx.voice_client:
            return
        
        button.disabled = True
        if self.isPause:
            self.ctx.voice_client.resume()
            button.label = "⏸️ 중지"
            button.style = discord.ButtonStyle.secondary
            self.isPause = False
        else:
            # 현재 재생 시간을 추적하고 일시 정지
            self.ctx.voice_client.pause()
            button.label = "▶️ 재생"
            button.style = discord.ButtonStyle.danger
            self.isPause = True
            
        button.disabled = False
        self.pause_interaction = interaction
        await interaction.response.edit_message(view=self)
    

    @discord.ui.button(label="⏭️ 다음곡", style=discord.ButtonStyle.secondary, row=0)
    async def next_button(self, button, interaction):
        if not self.ctx.voice_client:
            return

        if len(self.playStorage.get_all_entries()) <= 1 and self.playStorage.isRotate == "noRotate":
            button.disabled = True
            button.style = discord.ButtonStyle.danger
            button.label = "다음곡X"
            await interaction.response.edit_message(view=self)
            await asyncio.sleep(1)

            button.disabled = False
            button.style = discord.ButtonStyle.secondary
            button.label = "⏭️ 다음곡"
            await interaction.edit_original_response(view=self)
            return

        await interaction.response.edit_message(view=self)
        
        self.isNext = True
        self.ctx.voice_client.stop()
        self.playStorage.move_elements()
        await self.next_play()
        self.isNext = False
        

    # @discord.ui.select(
    #     placeholder="배속: 보통",
    #     min_values=1,
    #     max_values=1,
    #     options=[
    #         discord.SelectOption(label="2", value="2.0"),
    #         discord.SelectOption(label="1.75", value="1.75"),
    #         discord.SelectOption(label="1.5", value="1.5"),
    #         discord.SelectOption(label="1.25", value="1.25"),
    #         discord.SelectOption(label="보통", value="1.0"),
    #         discord.SelectOption(label="0.75", value="0.75"),
    #         discord.SelectOption(label="0.5", value="0.5"),
    #     ], row=2
    # )
    # async def atempo_callback(self, select, interaction: discord.Interaction):
    #     self.atempo = float(select.values[0])
    #     select.placeholder = f"배속: {float(self.atempo)}%"
    #     if not interaction.response.is_done():
    #         await interaction.response.edit_message(view=self)
    #     await self.atempo_update()

    # async def atempo_update(self):
    #     if not self.ctx.voice_client or not self.ctx.voice_client.is_playing():
    #         return

    #     # 기존 스트림 중지
    #     self.ctx.voice_client.stop()

    #     try:
    #         # 새로운 스트림 생성 및 재생
    #         source = discord.FFmpegPCMAudio(self.current_target_mp3 + ".mp3", **self.ffmpeg_options)
    #         transformed_source = PCMVolumeTransformer(source)
    #         transformed_source.volume = float(self.np_volume)

    #         self.ctx.voice_client.play(
    #             transformed_source,
    #             after=self.after_playing
    #         )
    #         print(f"Playback resumed with atempo={self.atempo}.")
    #     except Exception as e:
    #         print(f"Error updating atempo: {e}")

    @discord.ui.select(
        placeholder="볼륨: 100%",
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(label="100%", value="1.0"),
            discord.SelectOption(label="75%", value="0.75"),
            discord.SelectOption(label="50%", value="0.5"),
            discord.SelectOption(label="25%", value="0.25"),
            discord.SelectOption(label="10%", value="0.1"),
        ], row=2
    )
    async def volume_callback(self, select, interaction: discord.Interaction):
        self.np_volume = select.values[0]
        select.placeholder = f"볼륨: {float(self.np_volume) * 100:.0f}%"
        if not interaction.response.is_done():
            await interaction.response.edit_message(view=self)
        await self.volume_update()

    async def volume_update(self):
        if self.ctx.voice_client and self.ctx.voice_client.source:
            source = self.ctx.voice_client.source
            if isinstance(source, discord.PCMVolumeTransformer):
                volume = float(self.np_volume)
                source.volume = volume

nplay = None

@bot.slash_command(name="play", description="노래 재생")
async def play(ctx, *, url):
    global nplay
    if nplay is None:
        nplay = NowPlayManager()
    
    if not ctx.author.voice:
        await ctx.respond(":face_with_raised_eyebrow: ", ephemeral=True)
        await asyncio.sleep(5)
        await ctx.delete()
        return
    
    channel = ctx.author.voice.channel

    if ctx.voice_client:
        if not ctx.voice_client.channel == channel:
            await ctx.voice_client.move_to(channel)
    else:
        await channel.connect()
        # await ctx.respond(":grinning: ")
        # await asyncio.sleep(1)
        # await ctx.delete()

    await nplay.play(ctx, url)

@bot.slash_command(name="list", description="플레이 리스트 출력")
async def list(ctx):
    if nplay:
        playList = await nplay.list_show()
        if len(playList) == 0:
            await nplay.show_emotion(ctx, "리스트가 비어 있습니다.", 10)
        else:
            formatted_list = "\n".join(
                [f"{index + 1}. {item['title']} - {item['user']}" for index, item in enumerate(playList)]
            )
            await nplay.show_emotion(ctx, formatted_list, 10)
    else:
        await ctx.respond(f":sleeping: ", ephemeral=True)
        await asyncio.sleep(5)
        await ctx.delete()
        
@bot.slash_command(name="leave", description="나가")
async def leave(ctx):
    if ctx.voice_client:
        await nplay.show_emotion(ctx, ":smiling_face_with_tear: ", 5)
        await ctx.voice_client.disconnect()
        if nplay:
            # if nplay.ctx.voice_client and nplay.ctx.voice_client.is_playing():
            #     nplay.ctx.voice_client.stop()
            await nplay.cancel_downloads()
            await nplay.delete_screen_file()
            nplay.playStorage.clear_entries()
            nplay.isScreen = False
            nplay.isPlay = False
    else:
        await ctx.respond(f":angry:", ephemeral=True)
        await asyncio.sleep(5)
        await ctx.delete()

@bot.slash_command(name="help", description="도움!")
async def help(ctx):
    help_commend = """
    /play <url>    봇 접속 & 노래 추가
    /leave         봇 강퇴 & 노래 삭제
    /list          노래 재생 리스트 보이기기

    "THE BEER-WARE LICENSE" (Revision 42):
    * <staradian1999@gmail.com> wrote this file.  As long as you retain this notice you
    * can do whatever you want with this stuff. If we meet some day, and you think
    * this stuff is worth it, you can buy me a beer in return.   StaRadian
    """
    
    ctx.respond(help_commend, ephemeral=True)

@bot.event
async def on_ready():
    print(f"{bot.user} is ready and online!")
    try:
        synced = await bot.sync_commands()
    except Exception as e:
        print(f"Ready error: {e}")


@bot.event
async def on_disconnect():
    print(f"disconnect")
    if nplay and nplay.message:
        try:
            await nplay.delete_screen_file()  # 비동기 함수 안전하게 호출
            del nplay
        except Exception as e:
            print(f"Error while deleting message during disconnect: {e}")

@atexit.register
def on_shutdown():
    print("on_shutdown")
    try:
        loop = None
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        if loop.is_running():
            future = asyncio.run_coroutine_threadsafe(shutdown(), loop)
            future.result()
        else:
            loop.run_until_complete(shutdown())
    except RuntimeError as e:
        print(f"RuntimeError during shutdown: {e}")
    except Exception as e:
        print(f"Unexpected error during shutdown: {e}")
    finally:
        if loop and not loop.is_closed():
            loop.close()

async def shutdown():
    print("shutdown")
    try:
        if not bot.is_closed():
            if nplay and nplay.message:
                try:
                    await nplay.delete_screen_file()  # 메시지 삭제
                except Exception as e:
                    print(f"Error during shutdown: {e}")
            await bot.close()  # Discord 봇 종료
    except Exception as e:
        print(f"Unexpected error during shutdown: {e}")

bot.run(os.getenv('DISCORD_TOKEN')) # run the bot with the token