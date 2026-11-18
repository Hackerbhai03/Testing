# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# This file is part of AnonXMusic


import os
import re
import yt_dlp
import random
import asyncio
import aiohttp

from py_yt import Playlist, VideosSearch

from anony import config, logger
from anony.core import audio
from anony.helpers import Track, utils

# ── API config ────────────────────────────────────────────────
API_URL = "https://www.riteshyt.in"
API_KEY = "riteshpermanent_6e2e564b3b37175d82acfce7"


class DummyLogger:
    def debug(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


class YouTube:
    def __init__(self):
        self.base = "https://www.youtube.com/watch?v="
        self.listbase = "https://youtube.com/playlist?list="
        self.cookies = []
        self.checked = False
        self.cookie_dir = "anony/cookies"
        self.warned = False
        self._inflight: dict[tuple[str, bool], asyncio.Task] = {}
        self.regex = re.compile(
            r"(https?://)?(www\.|m\.|music\.)?"
            r"(youtube\.com/(watch\?v=|shorts/|playlist\?list=)|youtu\.be/)"
            r"([A-Za-z0-9_-]{11}|PL[A-Za-z0-9_-]+)([&?][^\s]*)?"
        )
        self.iregex = re.compile(
            r"https?://(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)"
            r"(?!/(watch\?v=[A-Za-z0-9_-]{11}|shorts/[A-Za-z0-9_-]{11}"
            r"|playlist\?list=PL[A-Za-z0-9_-]+|[A-Za-z0-9_-]{11}))\S*"
        )

    # ── helpers ───────────────────────────────────────────────
    @staticmethod
    def extract_vidid(q: str | None) -> str | None:
        if not q:
            return None
        if re.match(r"^[A-Za-z0-9_-]{11}$", q):
            return q
        m = re.search(
            r"(?:youtube\.com/(?:[^/]+/.+/|(?:v|e(?:mbed)?)/|shorts/|.*[?&]v=)|youtu\.be/)([^\s\"&?/]{11})",
            q,
        )
        return m.group(1) if m else None

    @staticmethod
    def _seconds(duration) -> int:
        try:
            return utils.to_seconds(duration) if duration not in (None, "None") else 0
        except Exception:
            return 0

    async def _api_json(self, path: str, **params):
        params = {k: v for k, v in params.items() if v is not None}
        params["api_key"] = API_KEY
        try:
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{API_URL}{path}", params=params) as resp:
                    if resp.status == 200:
                        return await resp.json(content_type=None)
                    logger.warning(f"API {path} returned {resp.status}")
        except Exception as ex:
            logger.warning(f"API {path} failed: {ex}")
        return None

    def _to_track(self, data: dict, m_id: int = 0, video: bool = False, user: str = None) -> Track | None:
        vid = data.get("id") or data.get("vidid")
        if not vid:
            return None
        channel = data.get("channel")
        channel_name = channel.get("name") if isinstance(channel, dict) else channel
        views = data.get("viewCount")
        view_count = views.get("short") if isinstance(views, dict) else data.get("view_count")
        thumbs = data.get("thumbnails") or []
        thumb = data.get("thumbnail") or (thumbs[-1].get("url") if thumbs else None)
        duration = data.get("duration") or data.get("duration_min") or "00:00"
        duration_sec = data.get("duration_sec") or self._seconds(duration)
        return Track(
            id=vid,
            channel_name=channel_name or "",
            duration=duration,
            duration_sec=int(duration_sec),
            message_id=m_id,
            title=(data.get("title") or "")[:25],
            thumbnail=thumb.split("?")[0] if thumb else None,
            url=data.get("link") or self.base + vid,
            user=user,
            view_count=view_count or "",
            video=video,
        )

    # ── cookies ───────────────────────────────────────────────
    def get_cookies(self):
        if not self.checked:
            for file in os.listdir(self.cookie_dir):
                if file.endswith(".txt"):
                    self.cookies.append(f"{self.cookie_dir}/{file}")
            self.checked = True
        if not self.cookies:
            if not self.warned:
                self.warned = True
                logger.warning("Cookies are missing; local downloads might fail.")
            return None
        return random.choice(self.cookies)

    async def save_cookies(self, urls: list[str]) -> None:
        logger.info("Saving cookies from urls...")
        async with aiohttp.ClientSession() as session:
            for url in urls:
                name = url.split("/")[-1]
                link = "https://batbin.me/raw/" + name
                async with session.get(link) as resp:
                    resp.raise_for_status()
                    with open(f"{self.cookie_dir}/{name}.txt", "wb") as fw:
                        fw.write(await resp.read())
        logger.info(f"Cookies saved in {self.cookie_dir}.")

    # ── validation ────────────────────────────────────────────
    def valid(self, url: str) -> bool:
        return bool(re.match(self.regex, url))

    def invalid(self, url: str) -> bool:
        return bool(re.match(self.iregex, url))

    # ── metadata ──────────────────────────────────────────────
    async def search(self, query: str, m_id: int, video: bool = False) -> Track | None:
        vid = self.extract_vidid(query)
        if vid:
            d = await self._api_json("/details", link=self.base + vid)
            if d:
                track = self._to_track(d, m_id, video)
                if track:
                    return track
        else:
            d = await self._api_json("/search", query=query, limit=1)
            results = (d or {}).get("result") or []
            if results:
                track = self._to_track(results[0], m_id, video)
                if track:
                    return track

        # Fallback: local search
        try:
            results = await VideosSearch(query, limit=1, with_live=False).next()
        except Exception:
            return None
        if results and results["result"]:
            return self._to_track(results["result"][0], m_id, video)
        return None

    async def playlist(self, limit: int, user: str, url: str, video: bool) -> list[Track | None]:
        tracks = []
        d = await self._api_json("/playlist", link=url, limit=limit)
        videos = (d or {}).get("videos") if isinstance(d, dict) else None
        if videos:
            for data in videos[:limit]:
                track = self._to_track(data, user=user, video=video)
                if track:
                    tracks.append(track)
            return tracks

        # Fallback: local playlist fetch
        try:
            plist = await Playlist.get(url)
            for data in plist["videos"][:limit]:
                track = self._to_track(data, user=user, video=video)
                if track:
                    track.url = (data.get("link") or track.url).split("&list=")[0]
                    tracks.append(track)
        except Exception:
            pass
        return tracks

    def cached(self, video_id: str, video: bool = False) -> str | None:
        return audio.find_cached(video_id, video)

    # ── download ──────────────────────────────────────────────
    async def download(self, video_id: str, video: bool = False) -> str | None:
        """Download (or reuse) a track. Concurrent calls for the same track share one download."""
        if path := audio.find_cached(video_id, video):
            return path

        key = (video_id, video)
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._download(video_id, video))
            self._inflight[key] = task
            task.add_done_callback(lambda _: self._inflight.pop(key, None))
        return await asyncio.shield(task)

    async def _download_api(self, video_id: str, video: bool) -> str | None:
        """Stream the file from the API into downloads/ and return its path."""
        dl_type = "video" if video else "audio"
        ext = "mp4" if video else "mp3"
        fp = f"{audio.DOWNLOAD_DIR}/{video_id}.{ext}"
        tmp = fp + ".part"
        params = {"query": video_id, "dl_type": dl_type, "api_key": API_KEY}
        os.makedirs(audio.DOWNLOAD_DIR, exist_ok=True)
        try:
            timeout = aiohttp.ClientTimeout(total=None, sock_read=600)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{API_URL}/download", params=params) as resp:
                    if resp.status != 200:
                        logger.warning(f"API download {video_id} returned {resp.status}")
                        return None
                    with open(tmp, "wb") as f:
                        async for chunk in resp.content.iter_chunked(131072):
                            f.write(chunk)
            if os.path.getsize(tmp) == 0:
                os.remove(tmp)
                return None
            os.replace(tmp, fp)
            return fp
        except Exception as ex:
            logger.warning(f"API download failed for {video_id}: {ex}")
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            return None

    async def _download(self, video_id: str, video: bool) -> str | None:
        path = await self._download_api(video_id, video)

        if not path:
            # Fallback: local yt-dlp download
            url = self.base + video_id
            opts = audio.ydl_options(video, self.get_cookies(), config.AUDIO_BACKEND)
            opts["logger"] = DummyLogger()

            def _run():
                with yt_dlp.YoutubeDL(opts) as ydl:
                    try:
                        ydl.download([url])
                    except (yt_dlp.utils.DownloadError, yt_dlp.utils.ExtractorError):
                        return None
                    except Exception as ex:
                        logger.warning("Download failed: %s", ex)
                        return None
                return audio.find_cached(video_id, video)

            path = await asyncio.to_thread(_run)

        if path:
            from anony import queue  # defined after this module is imported
            protected = {m.file_path for q in queue.queues.values() for m in q}
            protected.add(path)
            await asyncio.to_thread(audio.prune, config.CACHE_LIMIT_MB, protected)
        return path
