#coding: utf-8
from youtube_dl_test import YouTube
import youtube_dl
import downloader
import downloader_v3
from io import BytesIO
from constants import empty_thumbnail, isdeleted
from error_printer import print_error
#from youtube_mp3 import get_mp3
from timee import sleep
import re
from utils import urljoin, Downloader, Soup, try_n, get_print, filter_range, get_p2f, LazyUrl, query_url, compatstr, uuid, get_max_range, format_filename, clean_title, get_resolution, get_abr
import ffmpeg
import sys
import constants
import requests
import chardet
import os
import srt_converter
from random import randrange
import utils
from PyQt import QtCore, QtGui
from translator import tr_
from m3u8_tools import dash2stream


def print_streams(streams, cw):
    print_ = get_print(cw)
            
    for stream in streams:
        print_(u'[{}][{}fps][{}] {} [{} / {}] ─ {}'.format(stream.resolution, stream.fps, stream.abr, stream.subtype, stream.video_codec, stream.audio_codec, stream.format))
    print_('')


class Video(object):
    _url = None
    vcodec = None
    
    def __init__(self, url, type='video', only_mp4=False, audio_included=False, max_res=None, max_abr=None, cw=None):
        self.type = type
        self.only_mp4 = only_mp4
        self.audio_included = audio_included
        self.max_res = max_res
        self.max_abr = max_abr
        self.cw = cw
        self.url = LazyUrl(url, self.get, self, pp=self.pp, detect_local=False)
        self.exec_queue = cw.exec_queue if cw else None#
        
    def get(self, url, force=False):
        if self._url:
            return self._url
        
        type = self.type
        only_mp4 = self.only_mp4
        audio_included = self.audio_included
        max_res = self.max_res
        max_abr = self.max_abr
        cw = self.cw
        print_ = get_print(cw)

        if force:
            max_abr = 0

        print('max_res: {}'.format(max_res))
        for try_ in range(8):
            try:
                yt = YouTube(url)
                break
            except Exception as e:
                e_ = e
                s = print_error(e)[-1]
                print_('### youtube retry...\n{}'.format(s))
                sleep(try_/2.)
        else:
            raise e_

        streams = yt.streams.all()
        print_streams(streams, cw)

        if type == 'video':
            streams[:] = [stream for stream in streams if stream.video_codec is not None]
            # Only mp4
            if only_mp4:
                streams_ = list(streams)
                streams[:] = []
                for stream in streams_:
                    if stream.subtype == 'mp4':
                        streams.append(stream)

            # Audio included; Non-HD
            if audio_included:
                streams_ = list(streams)
                streams[:] = []
                for stream in streams_:
                    if stream.audio_codec is not None:
                        streams.append(stream)

            # Maximum resolution
            streams_ = list(streams)
            streams[:] = []
            for stream in streams_:
                if stream.resolution is None:
                    continue
                res = int(stream.resolution.replace('p',''))
                if max_res is None or res <= max_res:
                    streams.append(stream)
            print_('')
        elif type == 'audio':
            streams[:] = [stream for stream in streams if stream.abr]
            # Maximum abr
            abrs = [int_(stream.abr.replace('kbps', '')) for stream in streams]
            max_abr = min(max(abrs), max_abr)
            streams_ = list(streams)
            streams[:] = []
            for stream in streams_:
                if stream.abr is None:
                    continue
                abr = int(stream.abr.replace('kbps',''))
                if max_abr is None or abr >= max_abr:
                    streams.append(stream)
            #'''
        else:
            raise Exception(u'type "{}" is not supported'.format(type))

        # Pick the best
        while streams:
            if type == 'video':
                ress = [int_(stream.resolution.replace('p', '')) for stream in streams]
                m = max(ress)
                prefer_format = 'mp4'
            elif type == 'audio':
                ress = [int_(stream.abr.replace('kbps', '')) for stream in streams]
                m = min(ress)
                prefer_format = 'webm'
            print('Resolutions:', ress)
            stream_final = None
            for stream, res in zip(streams, ress):
                if res == m:
                    if type == 'video':
                        foo = (stream_final is not None) and (stream_final.audio_codec is None) and bool(stream.audio_codec)
                    elif type == 'audio':
                        foo = False
                    if stream_final is None or (stream_final.fps <= stream.fps and (foo or (stream_final.subtype.lower()!=prefer_format and stream.subtype.lower()==prefer_format) or stream_final.fps < stream.fps)):
                        #print(foo)
                        print_(u'# stream_final {} {} {} {} {} {}fps'.format(stream, stream.format, stream.resolution, stream.subtype, stream.audio_codec, stream.fps))
                        stream_final = stream
            
            ok = downloader.ok_url(stream_final.url, referer=url) if isinstance(stream_final.url, str) else True
            if ok:
                break
            else:
                print_('stream is not valid')
                streams.remove(stream_final)
        else:
            if type == 'audio' and not force:
                return self.get(url, force=True) # 1776
            raise Exception('No videos')

        stream = stream_final

##        if stream.video_codec and stream_final.video_codec.lower().startswith('av'):
##            self.vcodec = 'h264'
        
        self.yt = yt
        self.id = yt.video_id
        self.stream = stream
        self.username = yt.info['uploader']
        self.stream_audio = None
        self.audio = None
        self.thumb = None
        self.thumb_url = None
        self.subtitles = yt.subtitles

        if type == 'audio' and 'DASH' in self.stream.format:
            self.stream.setDashType('audio')

        # Audio
        if type=='video' and stream.audio_codec is None:
            print('audio required')
            streams = [stream for stream in yt.streams.all() if stream.abr]
            print_streams(streams, cw)
            # only mp4; https://github.com/KurtBestor/Hitomi-Downloader-issues/issues/480
            def isGood(stream):
                return stream.audio_codec.lower().startswith('mp4')
            if any(isGood(stream) for stream in streams):
                streams = [stream for stream in streams if isGood(stream)]
                print_streams(streams, cw)
            # only audio?
            if any(stream.resolution is None for stream in streams):
                streams = [stream for stream in streams if stream.resolution is None]
                print_streams(streams, cw)
            best_audio = None
            best_abr = 0
            for stream in streams:
                abr = int(stream.abr.replace('kbps', ''))
                if abr > best_abr:
                    best_abr = abr
                    best_audio = stream
            if best_audio is None:
                raise Exception('No audio')
            print(best_audio)
            self.stream_audio = best_audio
            if 'DASH' in self.stream_audio.format:
                self.stream_audio.setDashType('audio')
            self.audio = best_audio.url
            if callable(self.audio):
                self.audio = self.audio()

        # Thumbnail
        for quality in ['sddefault', 'hqdefault', 'mqdefault', 'default']:
            print('####', yt.thumbnail_url)
            self.thumb_url = yt.thumbnail_url.replace('default', quality)
            f = BytesIO()
            try:
                downloader.download(self.thumb_url, buffer=f)
                data = f.read()
                if len(data) == 0:
                    raise AssertionError('Zero thumbnail')
                if data == empty_thumbnail:
                    raise AssertionError('Empty thumbnail')
                f.seek(0)
                break
            except Exception as e:
                print(print_error(e)[-1])
        self.thumb = f

        #
        _url = self.stream.url
        if callable(_url):
            _url = _url()
        self._url = _url
        title = yt.title
        #soup = Soup(yt.watch_html)
        #title =  soup.title.text.replace('- YouTube', '').strip()
        self.title = title
        ext = u'.' + self.stream.subtype
        self.filename = format_filename(title, self.id, ext)

        print_(u'Resolution: {}'.format(stream.resolution))
        print_(u'Codec: {} / {}'.format(stream.video_codec, stream.audio_codec))
        print_(u'Abr: {}'.format(stream.abr))
        print_(u'Subtype: {}'.format(stream.subtype))
        print_(u'FPS: {}\n'.format(stream.fps))

        return self._url

    def pp(self, filename):
        cw = self.cw
        if cw:
            with cw.convert(self):
                return self._pp(filename)
        else:
            return self._pp(filename)

    def _pp(self, filename):
        cw = self.cw
        print_ = get_print(cw)
        ui_setting = utils.ui_setting
        ext = os.path.splitext(filename)[1].lower()
        if not os.path.isfile(filename):
            print(u'no file: {}'.format(filename))
            return
        
        filename_new = None
        if self.type == 'video' and (self.audio is not None or ext != '.mp4'): # UHD or non-mp4
            if self.audio is not None: # merge
                print_(u'Download audio: {}'.format(self.audio))
                hash = uuid()
                path = os.path.join(os.path.dirname(filename), '{}_a.tmp'.format(hash))
                if cw is not None:
                    cw.trash_can.append(path)
                if constants.FAST:
                    downloader_v3.download(self.audio, chunk=1024*1024, n_threads=2, outdir=os.path.dirname(path), fileName=os.path.basename(path), customWidget=cw, overwrite=True)
                else:
                    downloader.download(self.audio, outdir=os.path.dirname(path), fileName=os.path.basename(path), customWidget=cw, overwrite=True)
                ext, out = ffmpeg.merge(filename, path, cw=cw, vcodec=self.vcodec)
                #print(out)
                name, ext_old = os.path.splitext(filename)
                if ext_old.lower() != ext.lower():
                    print_(u'rename ext {} --> {}'.format(ext_old, ext))
                    filename_new = u'{}{}'.format(name, ext)
                    if os.path.isfile(filename_new):
                        os.remove(filename_new)
                    os.rename(filename, filename_new)
            else: # convert non-mp4 video -> mp4
                name, ext_old = os.path.splitext(filename)
                filename_new = u'{}.mp4'.format(name)
                print_(u'Convert video: {} -> {}'.format(filename, filename_new))
                ffmpeg.convert(filename, filename_new, cw=cw)
        elif self.type == 'audio' and ext != '.mp3': # convert non-mp3 audio -> mp3
            name, ext_old = os.path.splitext(filename)
            filename_new = u'{}.mp3'.format(name)
            abr = get_abr()
            ffmpeg.convert(filename, filename_new, '-shortest -preset ultrafast -b:a {}k'.format(abr), cw=cw)

        if self.type == 'audio' and ui_setting.albumArt.isChecked():
            try:
                self.thumb.seek(0)#
                ffmpeg.add_cover(filename_new, self.thumb, {'artist':self.username, 'title':self.title}, cw=cw)
            except Exception as e:
                s = print_error(e)[-1]
                print_(s)

        if ui_setting and ui_setting.subtitle.isChecked():
            lang = {'korean': 'ko', 'english': 'en', 'japanese': 'ja'}[compatstr(ui_setting.subtitleCombo.currentText()).lower()]
            if lang in self.subtitles:
                try:
                    subtitle = self.subtitles[lang]
                    filename_sub = u'{}.vtt'.format(os.path.splitext(filename)[0])
                    downloader.download(subtitle, os.path.dirname(filename_sub), fileName=os.path.basename(filename_sub), overwrite=True)
                    filename_sub_new = u'{}.srt'.format(os.path.splitext(filename_sub)[0])
                    cw.imgs.append(filename_sub_new)
                    cw.dones.add(os.path.realpath(filename_sub_new).replace('\\\\?\\', ''))
                    srt_converter.convert(filename_sub, filename_sub_new)
                    cw.setSubtitle(True)
                finally:
                    try:
                        os.remove(filename_sub)
                    except:
                        pass

        return filename_new


@Downloader.register
class Downloader_youtube(Downloader):
    type = 'youtube'
    single = True
    yt_type = None
    URLS = ['youtube.co', 'youtu.be']
    lock = True
    display_name = 'YouTube'
    
    def init(self):
        ui_setting = self.ui_setting
        if 'youtube_' in self.url:
            self.url = u'https://www.youtube.com/watch?v={}'.format(self.url.replace('youtube_',''))
            
        if self.customWidget.format:
            ext_result = self.customWidget.format
        else:
            ext_result = compatstr(ui_setting.youtubeCombo_type.currentText()).lower().split()[0]
            self.customWidget.format = ext_result
            
        if ext_result in ['mp4', 'mkv', '3gp']:
            self.yt_type = 'video'
        else:
            self.yt_type = 'audio'
            self.customWidget.setMusic(True)

    @classmethod
    def fix_url(cls, url): # 2033
        qs = query_url(url)
        if 'v' in qs:
            url = url.split('?')[0] + '?v={}'.format(qs['v'][0])
        return url
    
    def read(self):
        ui_setting = self.ui_setting
        cw = self.customWidget
        print_ = get_print(cw)
        if self.yt_type == 'video':
            res = get_resolution()
            info = get_videos(self.url, type=self.yt_type, max_res=res, only_mp4=False, audio_included=not True, cw=cw)
        else:
            abr = get_abr()
            info = get_videos(self.url, type=self.yt_type, max_abr=abr, cw=cw)
        videos = info['videos']

        cw.enableSegment(overwrite=True)

        # first video must be valid
        while videos:
            video = videos[0]
            try:
                video.url()
                break
            except Exception as e:
                print(e)
                videos.remove(video)
        else:
            raise Exception('No videos')

        if len(videos) > 1:
            p2f = get_p2f(cw)
            if p2f:
                self.single = False
                self.title = clean_title(info['title'])
                self.urls = [video.url for video in videos]
                video = videos[0]
                self.setIcon(video.thumb)
                return
            else:
                video = videos.pop(0)
                cw.gal_num = cw.url = video.url._url
                if videos and cw.alive:
                    s = u', '.join(video.url._url for video in videos)
                    self.exec_queue.put(([s, {'youtube':cw.format}], 'downButton(cw[0], format_selector=cw[1])'))

        self.urls.append(video.url)
        self.artist = video.username
        self.setIcon(video.thumb)
        
        self.title = video.title


def int_(x):
    try:
        return int(x)
    except:
        return 0

        
@try_n(2, sleep=1)
def get_videos(url, type='video', only_mp4=False, audio_included=False, max_res=None, max_abr=None, cw=None):
    info = {}

    n = get_max_range(cw, 2000)
    
    if '/channel/' in url or '/user/' in url or '/c/' in url:
        info = read_channel(url, n=n)
        info['title'] = u'[Channel] {}'.format(info['uploader'])
        if cw:
            info['urls'] = filter_range(info['urls'], cw.range)
    elif '/playlist' in url:
        info = read_playlist(url, n=n)
        info['title'] = u'[Playlist] {}'.format(info['title'])
        if cw:
            info['urls'] = filter_range(info['urls'], cw.range)
    else:
        info['urls'] = [url]

    info['videos'] = [Video(url, type, only_mp4, audio_included, max_res, max_abr, cw) for url in info['urls']]

    return info



def read_channel(url, n):
    options = {
            'extract_flat': True,
            }
    ydl = youtube_dl.YoutubeDL(options)
    info = ydl.extract_info(url)

    info = read_playlist(info['url'], n=n)
                
    return info


@try_n(2)
def read_playlist(url, n):
    options = {
            'extract_flat': True,
            'playlistend': n,
            }
    ydl = youtube_dl.YoutubeDL(options)
    info = ydl.extract_info(url)

    es = info['entries']
    urls = []
    for e in es:
        href = 'https://www.youtube.com/watch?v={}'.format(e['id'])
        urls.append(href)
    info['urls'] = urls

    return info


import selector
@selector.register('youtube')
def select():
    if utils.ui_setting.askYoutube.isChecked():
        value = utils.messageBox(tr_(u'Youtube format?'), icon=QtGui.QMessageBox.Question, buttons=[tr_(u'MP4 (동영상)'), tr_(u'MP3 (음원)')])
        format = ['mp4', 'mp3'][value]
        return format


