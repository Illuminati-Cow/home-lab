import sys
import subprocess
import json
import os
import shutil
import re
from datetime import datetime
import curses
import curses.textpad
import tempfile
import glob
import readline
from typing import List
import abc
import pprint

class VideoDownloader(abc.ABC):
    @abc.abstractmethod
    def download(self, url: str, audio_format: str, video_format: str) -> str:
        pass


class RealVideoDownloader(VideoDownloader):
    def __init__(self, audio_formats, video_formats):
        self.audio_formats = audio_formats
        self.video_formats = video_formats

    def download(self, url: str, audio_format: str, video_format: str) -> str:
        if audio_format != 'none' and video_format == 'none':
            # Audio only
            ext = self.audio_formats[audio_format]['ext']
            bitrate = self.audio_formats[audio_format]['bitrate']
            if bitrate is not None:
                quality = bitrate // 1000
                cmd = ['yt-dlp', '-f', f'ba[abr>={quality}]', '--write-thumbnail', '--convert-thumbnails', 'jpg', '--output', f'temp_audio.%(ext)s', url]
            else:
                cmd = ['yt-dlp', '-f', 'bestaudio', '-S', 'abr', '--write-thumbnail', '--convert-thumbnails', 'jpg', '--output', f'temp_audio.%(ext)s', url]
            print("Downloading audio...")
            subprocess.run(cmd, check=True)
            video_files = glob.glob('temp_audio.*')
            if not video_files:
                raise RuntimeError("Downloaded audio file not found.")
            video_files.remove(next(f for f in video_files if f.endswith('.jpg')))
            video_file = video_files[0]
            if audio_format == 'wav':
                wav_file = 'temp_audio.wav'
                cmd = f'ffmpeg -i "{video_file}" -c:a pcm_s16le "{wav_file}"'
                subprocess.run(cmd, shell=True, check=True)
                os.remove(video_file)
                video_file = wav_file
        elif video_format != 'none' and audio_format == 'none':
            ext = self.video_formats[video_format]['ext']
            height = self.video_formats[video_format]['height']
            cmd = ['yt-dlp', '-f', f'bestvideo[height<={height}]+bestaudio/best[height<={height}]', '-S', 'height', '--recode', ext, '--write-thumbnail', '--convert-thumbnails', 'jpg', '--output', f'temp_video.%(ext)s', url]
            print("Downloading video...")
            subprocess.run(cmd, check=True)
            video_files = glob.glob('temp_video.*')
            if not video_files:
                raise RuntimeError("Downloaded video file not found.")
            video_files.remove(next(f for f in video_files if f.endswith('.jpg')))
            video_file = video_files[0]
        else:
            # Both
            cmd = ['yt-dlp', '--format', 'best', '-S', 'res', '--write-thumbnail', '--convert-thumbnails', 'jpg', '--output', 'temp_video.%(ext)s', url]
            print("Downloading video...")
            subprocess.run(cmd, check=True)
            video_files = glob.glob('temp_video.*')
            if not video_files:
                raise RuntimeError("Downloaded video file not found.")
            video_files.remove(next(f for f in video_files if f.endswith('.jpg')))
            video_file = video_files[0]
        return video_file


class MockVideoDownloader(VideoDownloader):
    def __init__(self, source_dir: str):
        self.source_dir = source_dir

    def download(self, url: str, audio_format: str, video_format: str) -> str:
        source_file = os.path.join(self.source_dir, 'temp_video.mp4')
        if not os.path.exists(source_file):
            raise FileNotFoundError(f"Mock video file not found: {source_file}")
        shutil.copy(source_file, 'temp_video.mp4')
        return 'temp_video.mp4'


class InfoFetcher(abc.ABC):
    @abc.abstractmethod
    def fetch_info(self, url: str) -> dict:
        pass


class RealInfoFetcher(InfoFetcher):
    def fetch_info(self, url: str) -> dict:
        print("Fetching video information...")
        try:
            result = subprocess.run(['yt-dlp', '--dump-json', url], capture_output=True, check=True, text=True)
            return json.loads(result.stdout)
        except subprocess.CalledProcessError as e:
            print(f"Error fetching video information: {e.stderr}")
            sys.exit(1)


class MockInfoFetcher(InfoFetcher):
    def fetch_info(self, url: str) -> dict:
        return {
            "title": "GOTEC - PRADA2000 | HÖR - May 29 / 2024",
            "description": "GOTEC - PRADA2000 live from our studio in Berlin.",
            "duration": 3600
        }

# Define audio and video formats with commands and size estimation
audio_formats = {
    'none': {'name': 'None', 'bitrate': 0, 'ext': None, 'command': ''},
    'mp3_128': {'name': 'MP3 128kbps', 'bitrate': 128000, 'ext': 'mp3', 'command': '-b:a 128k'},
    'mp3_320': {'name': 'MP3 320kbps', 'bitrate': 320000, 'ext': 'mp3', 'command': '-b:a 320k'},
    'flac': {'name': 'FLAC (Lossless)', 'bitrate': None, 'ext': 'flac', 'command': '-c:a flac'},
    'wav': {'name': 'WAV (Lossless)', 'bitrate': None, 'ext': 'wav', 'command': '-c:a pcm_s16le'},
}

video_formats = {
    'none': {'name': 'None', 'bitrate': 0, 'ext': None, 'height': None, 'command': ''},
    'mp4_720p': {'name': 'MP4 720p', 'bitrate': 2000000, 'ext': 'mp4', 'height': 720, 'command': '-vf scale=-2:720 -c:v libx264 -b:v 2M -c:a aac'},
    'mp4_1080p': {'name': 'MP4 1080p', 'bitrate': 5000000, 'ext': 'mp4', 'height': 1080, 'command': '-vf scale=-2:1080 -c:v libx264 -b:v 5M -c:a aac'},
    'mkv_1080p': {'name': 'MKV 1080p', 'bitrate': 5000000, 'ext': 'mkv', 'height': 1080, 'command': '-vf scale=-2:1080 -c:v libx264 -b:v 5M -c:a aac'},
}

audio_valid = [k for k in audio_formats.keys()]
video_valid = [k for k in video_formats.keys()]

if len(sys.argv) < 2:
    print("Usage: python get_dj_set.py <youtube_url>")
    sys.exit(1)

url = None
mock = False
jellyfin_path = None
no_jellyfin = False
auto_metadata = False
audio_format = 'none'
video_format = 'none'

args = sys.argv[1:]
i = 0
while i < len(args):
    arg = args[i]
    if arg in ('--help', '-h'):
        print("Usage: python get_dj_set.py [OPTIONS] URL")
        print("Downloads a DJ set from the provided YouTube URL, allows metadata editing,")
        print("and imports it into a specified Jellyfin music library.")
        print("Options:")
        print("  --mock                             Use mock implementations for info fetching and video downloading for testing")
        print("  --jellyfin-library PATH, -jf PATH  Specify the Jellyfin library path (skips prompt if valid)")
        print("  --no-jellyfin, -nojf               Skip Jellyfin library import")
        print("  --auto-metadata, -am               Use extracted metadata without interactive editing")
        print(f"  --audio-format, -af FORMAT         Specify audio format {audio_valid}")
        print(f"  --video-format, -vf FORMAT         Specify video format {video_valid}")
        sys.exit(0)
    if arg in ('--version', '-v'):
        print("get_dj_set.py version 0.1")
        sys.exit(0)
    if arg in ('--library-path', '-l'):
        print ("The --library-path option is not yet supported. Please provide the Jellyfin library path when prompted.")
        sys.exit(1)
    if arg == '--mock':
        mock = True
    elif arg in ('--jellyfin-library', '-jf'):
        i += 1
        if i >= len(args):
            print("Error: --jellyfin-library requires a path argument")
            sys.exit(1)
        jellyfin_path = args[i]
    elif arg in ('--no-jellyfin', '-nojf'):
        no_jellyfin = True
    elif arg in ('--auto-metadata', '-am'):
        auto_metadata = True
    elif arg in ('--audio-format', '-af'):
        i += 1
        if i >= len(args):
            print("Error: --audio-format requires a format argument")
            sys.exit(1)
        audio_format = args[i]
        if audio_format not in audio_valid:
            print(f"Error: Invalid audio format '{audio_format}'. Valid options: {audio_valid}")
            sys.exit(1)
    elif arg in ('--video-format', '-vf'):
        i += 1
        if i >= len(args):
            print("Error: --video-format requires a format argument")
            sys.exit(1)
        video_format = args[i]
        if video_format not in video_valid:
            print(f"Error: Invalid video format '{video_format}'. Valid options: {video_valid}")
            sys.exit(1)
    elif arg.startswith('-'):
        print(f"Unknown option: {arg}")
        sys.exit(1)
    elif url is None:
        url = arg
    else:
        print(f"Unexpected argument: {arg}")
        sys.exit(1)
    i += 1

# Check if yt-dlp is installed
try:
    subprocess.run(['yt-dlp', '--version'], capture_output=True, check=True, text=True)
except subprocess.CalledProcessError:
    print("Error: yt-dlp is not installed or not accessible. Please install yt-dlp.")
    sys.exit(1)

# Check if ffmpeg is installed
try:
    subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True, text=True)
except subprocess.CalledProcessError:
    print("Error: ffmpeg is not installed or not accessible. Please install ffmpeg.")
    sys.exit(1)

if mock:
    info_fetcher = MockInfoFetcher()
else:
    info_fetcher = RealInfoFetcher()

if url is None and not mock:
    print("Error: No URL provided.")
    sys.exit(1)

info = info_fetcher.fetch_info(url)
title = info.get('title', 'Unknown Title')
description = info.get('description', 'No description')
duration = info.get('duration', 0)

def parse_date(date_str):
    """Parse date from various formats and return datetime object or None."""
    if not date_str:
        return None
    
    # Try different formats
    formats = [
        '%Y-%m-%d',  # 2024-05-29
        '%B %d / %Y',  # May 29 / 2024
        '%b %d / %Y',  # May 29 / 2024 (abbreviated month)
        '%m/%d/%Y',  # 05/29/2024
        '%d/%m/%Y',  # 29/05/2024
        '%Y/%m/%d',  # 2024/05/29
        '%Y%m%d',  # 20240529 (yt-dlp upload_date)
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    return None

# Parse metadata from title
# Common DJ set title formats: "DJ Name - Set Title (Genre) [Date]" or variations
metadata = {'title': title, 'artist': 'Unknown DJ', 'release_date': None}

# Regex patterns to try
patterns = [
    r'^(.*?) - (.*?) \| HÖR (?:.*?)- (.+)$',  # HOR Berlin Format: Title - DJ | HÖR - Date
    r'^(.*?) \| HÖR (?:.*?)- (.+)$',  # HOR Berlin Format: DJ | HÖR - Date
    r'^(.*?) \| HÖR .+$'  # HOR Berlin Format: DJ | HÖR
    r'^(.*?) \| .+$',  # Boiler Room Format: DJ | Boiler Room: Location
]

for pattern in patterns:
    match = re.match(pattern, title)
    if match:
        groups = match.groups()
        if len(groups) == 3:
            metadata['title'] = groups[0].strip()
            metadata['artist'] = groups[1].strip()
            metadata['release_date'] = parse_date(groups[2].strip())
        elif len(groups) == 2:
            metadata['artist'] = groups[0].strip()
            metadata['release_date'] = parse_date(groups[1].strip())
        elif len(groups) == 1:
            metadata['artist'] = groups[0].strip()
        break

# Try to parse date from description if not found
if metadata['release_date'] is None:
    # Look for various date patterns in description
    date_patterns = [
        r'\b(\d{4}-\d{2}-\d{2})\b',  # 2024-05-29
        r'\b([A-Za-z]+ \d{1,2} / \d{4})\b',  # May 29 / 2024
        r'\b(\d{1,2}/\d{1,2}/\d{4})\b',  # 05/29/2024 or 29/05/2024
        r'\b(\d{4}/\d{1,2}/\d{1,2})\b',  # 2024/05/29
    ]
    
    for pattern in date_patterns:
        match = re.search(pattern, description)
        if match:
            parsed_date = parse_date(match.group(1))
            if parsed_date:
                metadata['release_date'] = parsed_date
                break
    else:
        upload_date = info.get('upload_date')
        if upload_date:
            metadata['release_date'] = parse_date(upload_date)

# Handle Jellyfin library path
if not no_jellyfin:
    if jellyfin_path is None:
        jellyfin_path = input("Please enter the full path to your Jellyfin music library: ")
    # Validate the path
    if not os.path.isdir(jellyfin_path):
        print("Error: The specified path is not a valid directory.")
        sys.exit(1)

# Interactive metadata editing using curses
def format_metadata_value(metadata, field):
    value = metadata[field]
    if field == 'release_date':
        if isinstance(value, datetime):
            return value.strftime('%Y-%m-%d')
        elif value is None:
            return 'Unknown'
        else:
            return str(value)
    return str(value)

def edit_metadata(stdscr: curses.window, metadata: dict) -> dict:
    curses.curs_set(0)
    stdscr.clear()
    stdscr.addstr(0, 0, "Edit Metadata (Use arrow keys to select, Enter to edit, Ctrl+C to confirm):")

    fields: List[str] = list(metadata.keys())
    fields.append('Done')
    current_idx = 0
    while True:
        for i, field in enumerate(fields):
            if i == current_idx:
                if field == 'Done':
                    stdscr.addstr(i + 2, 0, f"> {field}", curses.A_REVERSE)
                else:
                    stdscr.addstr(i + 2, 0, f"> {field}: {format_metadata_value(metadata, field)}", curses.A_REVERSE)
            else:
                if field == 'Done':
                    stdscr.addstr(i + 2, 0, f"  {field}")
                else:
                    stdscr.addstr(i + 2, 0, f"  {field}: {format_metadata_value(metadata, field)}")
        
        stdscr.refresh()
        key = stdscr.getch()
        
        if key == curses.KEY_UP and current_idx > 0:
            current_idx -= 1
        elif key == curses.KEY_DOWN and current_idx < len(fields) - 1:
            current_idx += 1
        elif key == 10:  # Enter
            if fields[current_idx] == 'Done':
                fields.pop(current_idx)
                break

            curses.curs_set(1)
            field_name = fields[current_idx]
            current_value = format_metadata_value(metadata, field_name)
            max_len = 60
            cursors_y = current_idx + 2
            cursors_x = len(field_name) + 4

            # Create a single-line window at the cursor position and prefill it
            win = curses.newwin(1, max_len, cursors_y, cursors_x)
            win.addstr(0, 0, current_value)
            win.move(0, len(current_value))
            win.refresh()

            # Make Enter (newline) finish editing by mapping newline to Ctrl-G (ASCII 7)
            def _enter_terminator(ch):
                if ch == 10:  # Enter
                    return 7   # Ctrl-G ends Textbox.edit()
                return ch

            textbox = curses.textpad.Textbox(win)
            new_value = textbox.edit(_enter_terminator).strip()

            curses.curs_set(0)
            if new_value:
                if field_name == 'release_date':
                    try:
                        metadata[field_name] = datetime.fromisoformat(new_value)
                    except ValueError:
                        # If invalid, keep the old value or set to None
                        pass  # keep old
                else:
                    metadata[field_name] = new_value
    
    return metadata

if not auto_metadata:
    metadata = curses.wrapper(edit_metadata, metadata)
else:
    print("Using auto-extracted metadata:")
    pprint.pprint(metadata)



# Function to estimate file size
def estimate_size(bitrate, duration):
    if bitrate is None:
        # For lossless, approximate as 1411 kbps for CD quality audio
        return int(1411000 * duration / 8)
    return int(bitrate * duration / 8)

# Add size estimates
for fmt in audio_formats.values():
    fmt.setdefault('size', 0)
    if fmt.get('name') != 'None':
        fmt['size'] = estimate_size(fmt['bitrate'], duration)

for fmt in video_formats.values():
    fmt.setdefault('size', 0)
    if fmt.get('name') != 'None':
        fmt['size'] = estimate_size(fmt['bitrate'], duration)

# Interactive format selection using curses
def select_format(stdscr, formats, title_text):
    curses.curs_set(0)
    stdscr.clear()
    stdscr.addstr(0, 0, title_text)
    
    options = list(formats.keys())
    current_idx = 0
    while True:
        for i, opt in enumerate(options):
            fmt = formats[opt]
            size_mb = fmt['size'] / (1024 * 1024) if fmt['size'] > 0 else 0
            if i == current_idx:
                stdscr.addstr(i + 2, 0, f"> {fmt['name']} ({size_mb:.1f} MB)", curses.A_REVERSE)
            else:
                stdscr.addstr(i + 2, 0, f"  {fmt['name']} ({size_mb:.1f} MB)")
        
        stdscr.refresh()
        key = stdscr.getch()
        
        if key == curses.KEY_UP and current_idx > 0:
            current_idx -= 1
        elif key == curses.KEY_DOWN and current_idx < len(options) - 1:
            current_idx += 1
        elif key == 10:  # Enter
            return options[current_idx]

while audio_format == 'none' and video_format == 'none':
    audio_format = curses.wrapper(select_format, audio_formats, "Select Audio Format (Use arrow keys, Enter to select):")
    video_format = curses.wrapper(select_format, video_formats, "Select Video Format (Use arrow keys, Enter to select):")


# Create temporary directory for downloads
temp_dir = tempfile.mkdtemp()
original_cwd = os.getcwd()
os.chdir(temp_dir)

if mock:
    downloader = MockVideoDownloader(original_cwd)
else:
    downloader = RealVideoDownloader(audio_formats, video_formats)

try:
    video_file = downloader.download(url, audio_format, video_format)

except (RuntimeError, FileNotFoundError) as e:
    print(e)
    os.chdir(original_cwd)
    shutil.rmtree(temp_dir)
    sys.exit(1)

def sanitize_filename(name):
    # Remove invalid characters for filenames
    return re.sub(r'[<>:"/\\|?*]', '', name).strip()

def escape_metadata(value):
    # Escape quotes for ffmpeg metadata
    return value.replace('"', '\\"').replace("'", "\\'")

def get_metadata_date(metadata):
    if isinstance(metadata['release_date'], datetime):
        return str(metadata['release_date'].year)
    elif metadata['release_date'] is None:
        return ''
    else:
        return str(metadata['release_date'])

# Process audio if selected
audio_file = None
if audio_format != 'none':
    audio_ext = audio_formats[audio_format]['ext']
    audio_file = f"output_audio.{audio_ext}"
    metadata_flags = f'-metadata artist="{escape_metadata(metadata["artist"])}" -metadata title="{escape_metadata(metadata["title"])}" -metadata date="{escape_metadata(get_metadata_date(metadata))}"'
    cmd = f'ffmpeg -i "{video_file}" -vn {metadata_flags} {audio_formats[audio_format]["command"]} "{audio_file}"'
    print(f"Extracting audio to {audio_file}...")
    try:
        subprocess.run(cmd, shell=True, check=True)
        # Embed thumbnail using mutagen if available
        thumb_file = os.path.splitext(video_file)[0] + '.jpg'
        if os.path.exists(thumb_file):
            try:
                if audio_ext == 'mp3':
                    from mutagen.mp3 import MP3
                    from mutagen.id3 import APIC
                    audio = MP3(audio_file)
                    with open(thumb_file, 'rb') as f:
                        audio.tags.add(APIC(mime='image/jpeg', type=3, desc='Cover', data=f.read()))
                    audio.save()
                elif audio_ext == 'flac':
                    from mutagen.flac import FLAC, Picture
                    audio = FLAC(audio_file)
                    pic = Picture()
                    pic.type = 3
                    pic.desc = 'Cover'
                    with open(thumb_file, 'rb') as f:
                        pic.data = f.read()
                    pic.mime = 'image/jpeg'
                    audio.add_picture(pic)
                    audio.save()
                # For wav, no embedding
            except Exception as e:
                print(f"Warning: Could not embed thumbnail in audio: {e}")
    except subprocess.CalledProcessError as e:
        print(f"Error extracting audio: {e}")
        shutil.rmtree(temp_dir)
        sys.exit(1)

# Process video if selected
final_video_file = None
if video_format != 'none':
    video_ext = video_formats[video_format]['ext']
    final_video_file = f"output_video.{video_ext}"
    if video_formats[video_format]['command']:
        metadata_flags = f'-metadata artist="{escape_metadata(metadata["artist"])}" -metadata title="{escape_metadata(metadata["title"])}" -metadata date="{escape_metadata(get_metadata_date(metadata))}"'
        thumb_file = os.path.splitext(video_file)[0] + '.jpg'
        if os.path.exists(thumb_file):
            thumb_part = f'-i "{thumb_file}" -map 0 -map 1 -c copy -disposition:v:1 attached_pic '
        else:
            thumb_part = '-c copy '
        cmd = f'ffmpeg -i "{video_file}" {thumb_part} {metadata_flags} {video_formats[video_format]["command"]} "{final_video_file}"'
        print(f"Converting video to {final_video_file}...")
        try:
            subprocess.run(cmd, shell=True, check=True)
        except subprocess.CalledProcessError as e:
            print(f"Error converting video: {e}")
            shutil.rmtree(temp_dir)
            sys.exit(1)
    else:
        # If no command, just rename
        os.rename(video_file, final_video_file)

if video_format == 'none' and not mock:
    os.remove(video_file)

# Rename files using metadata
if audio_file:
    date_str = format_metadata_value(metadata, 'release_date')
    if date_str != 'Unknown':
        new_name = sanitize_filename(f"{metadata['artist']} - {metadata['title']} ({date_str}).{audio_formats[audio_format]['ext']}")
    else:
        new_name = sanitize_filename(f"{metadata['artist']} - {metadata['title']}.{audio_formats[audio_format]['ext']}")
    os.rename(audio_file, new_name)
    audio_file = new_name

if final_video_file:
    date_str = format_metadata_value(metadata, 'release_date')
    if date_str != 'Unknown':
        new_name = sanitize_filename(f"{metadata['artist']} - {metadata['title']} ({date_str}).{video_formats[video_format]['ext']}")
    else:
        new_name = sanitize_filename(f"{metadata['artist']} - {metadata['title']}.{video_formats[video_format]['ext']}")
    os.rename(final_video_file, new_name)
    final_video_file = new_name

# Path completion setup
matches = []
def complete_path(text, state):
    global matches
    if state == 0:
        if text.endswith('/'):
            dir_path = text
            base = ''
        else:
            dir_path = os.path.dirname(text) or '.'
            base = os.path.basename(text)
        try:
            files = os.listdir(dir_path)
            matches = [f for f in files if f.startswith(base)]
            matches = [os.path.join(dir_path, f) for f in matches]
            # If directory, add / at end
            matches = [m + '/' if os.path.isdir(m) else m for m in matches]
        except OSError:
            matches = []
    # Return the state-th match
    try:
        return matches[state]
    except IndexError:
        return None

readline.set_completer_delims(' \t\n')
readline.parse_and_bind('tab: complete')
readline.set_completer(complete_path)

files_to_copy = []
if audio_file:
    files_to_copy.append(audio_file)
if final_video_file:
    files_to_copy.append(final_video_file)

destination_path = os.path.join(original_cwd if no_jellyfin else jellyfin_path, sanitize_filename(metadata['artist']))
if not os.path.exists(destination_path):
    print(f"Creating directory {destination_path}...")
    os.makedirs(destination_path)
for file in files_to_copy:
    dst = os.path.join(destination_path, os.path.basename(file)) # type: ignore
    print(f"Importing {file} to {dst}...")
    try:
        shutil.move(file, dst)
        print(f"Moved {file} to {dst}")
    except OSError as e:
        print(f"Error moving {file}: {e}")
if no_jellyfin:
    print("Jellyfin import skipped as per user request, files are available in the current directory.")
else:
    print("Files imported successfully into Jellyfin library.")

print("Program completed.")