import sys
import subprocess
import json
import os
import shutil
import re
from datetime import datetime
import curses
import tempfile
import glob
import readline
from typing import List


# Check command line arguments
if len(sys.argv) < 2:
    print("Usage: python get_dj_set.py <youtube_url>")
    sys.exit(1)

url = None

for arg in sys.argv[1:]:
    if arg in ('--help', '-h'):
        print("Usage: python get_dj_set.py [OPTIONS] URL")
        print("Downloads a DJ set from the provided YouTube URL, allows metadata editing,")
        print("and imports it into a specified Jellyfin music library.")
        sys.exit(0)
    if arg in ('--version', '-v'):
        print("get_dj_set.py version 0.1")
        sys.exit(0)
    if arg in ('--library-path', '-l'):
        print ("The --library-path option is not yet supported. Please provide the Jellyfin library path when prompted.")
        sys.exit(1)
    elif arg.startswith('-'):
        print(f"Unknown option: {arg}")
        sys.exit(1)
    elif url is None:
        url = arg
    else:
        print(f"Unexpected argument: {arg}")
        sys.exit(1)

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

# Get video information using yt-dlp
def fetch_video_info(url, runner=subprocess.run):
    """
    Fetch video info using the provided runner (defaults to subprocess.run).
    The runner may be replaced with a mock that returns either:
      - an object with a 'stdout' attribute containing the JSON string,
      - a dict already (useful for very simple mocks),
      - or a JSON string.
    """
    print("Fetching video information...")
    try:
        result = runner(['yt-dlp', '--dump-json', url], capture_output=True, check=True, text=True)
        # Support multiple runner return shapes for easy mocking
        if isinstance(result, dict):
            return result
        if hasattr(result, 'stdout'):
            return json.loads(result.stdout)
        if isinstance(result, str):
            return json.loads(result)
        raise ValueError("Unexpected runner return type from fetch_video_info")
    except subprocess.CalledProcessError as e:
        print(f"Error fetching video information: {e.stderr}")
        sys.exit(1)

def mock_runner(cmd, capture_output=True, check=True, text=True):
    class MockResult:
        stdout = json.dumps({
            "title": "GOTEC - PRADA2000 | HÖR - May 29 / 2024",
            "description": "GOTEC - PRADA2000 live from our studio in Berlin.",
            "duration": 3600
        })
    return MockResult()


info = fetch_video_info(url)
title = info.get('title', 'Unknown Title')
description = info.get('description', 'No description')
duration = info.get('duration', 0)

# Parse metadata from title
# Common DJ set title formats: "DJ Name - Set Title (Genre) [Date]" or variations
metadata = {'title': title, 'artist': 'Unknown DJ', 'release_date': 'Unknown'}

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
            metadata['release_date'] = groups[2].strip()
        elif len(groups) == 2:
            metadata['artist'] = groups[0].strip()
            metadata['release_date'] = groups[1].strip()
        elif len(groups) == 1:
            metadata['artist'] = groups[0].strip()
        break

# Try to parse date from description if not found
if metadata['release_date'] == 'Unknown':
    date_match = re.search(r'\b(\d{4}-\d{2}-\d{2})\b', description)
    if date_match:
        metadata['release_date'] = date_match.group(1)

# Interactive metadata editing using curses
def edit_metadata(stdscr, metadata):
    curses.curs_set(0)
    stdscr.clear()
    stdscr.addstr(0, 0, "Edit Metadata (Use arrow keys to select, Enter to edit, Ctrl+C to confirm):")
    
    fields = list(metadata.keys())
    current_idx = 0
    while True:
        for i, field in enumerate(fields):
            if i == current_idx:
                stdscr.addstr(i + 2, 0, f"> {field}: {metadata[field]}", curses.A_REVERSE)
            else:
                stdscr.addstr(i + 2, 0, f"  {field}: {metadata[field]}")
        
        stdscr.refresh()
        key = stdscr.getch()
        
        if key == curses.KEY_UP and current_idx > 0:
            current_idx -= 1
        elif key == curses.KEY_DOWN and current_idx < len(fields) - 1:
            current_idx += 1
        elif key == 10:  # Enter
            stdscr.addstr(len(fields) + 3, 0, f"Edit {fields[current_idx]}: ")
            curses.echo()
            new_value = stdscr.getstr(len(fields) + 3, len(f"Edit {fields[current_idx]}: "), 100).decode('utf-8')
            curses.noecho()
            if new_value:
                metadata[fields[current_idx]] = new_value
        elif key == 3:  # Ctrl+C
            break
    
    return metadata

metadata = curses.wrapper(edit_metadata, metadata)

# Define audio and video formats with commands and size estimation
audio_formats = {
    'none': {'name': 'None', 'bitrate': 0, 'ext': None, 'command': ''},
    'mp3_128': {'name': 'MP3 128kbps', 'bitrate': 128000, 'ext': 'mp3', 'command': '-b:a 128k'},
    'mp3_320': {'name': 'MP3 320kbps', 'bitrate': 320000, 'ext': 'mp3', 'command': '-b:a 320k'},
    'flac': {'name': 'FLAC (Lossless)', 'bitrate': None, 'ext': 'flac', 'command': '-c:a flac'},
    'wav': {'name': 'WAV (Lossless)', 'bitrate': None, 'ext': 'wav', 'command': '-c:a pcm_s16le'},
}

video_formats = {
    'none': {'name': 'None', 'bitrate': 0, 'ext': None, 'command': ''},
    'mp4_720p': {'name': 'MP4 720p', 'bitrate': 2000000, 'ext': 'mp4', 'command': '-vf scale=-2:720 -c:v libx264 -b:v 2M -c:a aac'},
    'mp4_1080p': {'name': 'MP4 1080p', 'bitrate': 5000000, 'ext': 'mp4', 'command': '-vf scale=-2:1080 -c:v libx264 -b:v 5M -c:a aac'},
    'mkv_1080p': {'name': 'MKV 1080p', 'bitrate': 5000000, 'ext': 'mkv', 'command': '-vf scale=-2:1080 -c:v libx264 -b:v 5M -c:a aac'},
}

# Function to estimate file size
def estimate_size(bitrate, duration):
    if bitrate is None:
        # For lossless, approximate as 1411 kbps for CD quality audio
        return int(1411000 * duration / 8)
    return int(bitrate * duration / 8)

# Add size estimates
for fmt in audio_formats.values():
    if fmt['name'] != 'None':
        fmt['size'] = estimate_size(fmt['bitrate'], duration)

for fmt in video_formats.values():
    if fmt['name'] != 'None':
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

audio_format = 'none'
video_format = 'none'
while audio_format == 'none' and video_format == 'none':
    audio_format = curses.wrapper(select_format, audio_formats, "Select Audio Format (Use arrow keys, Enter to select):")
    video_format = curses.wrapper(select_format, video_formats, "Select Video Format (Use arrow keys, Enter to select):")


# Create temporary directory for downloads
temp_dir = tempfile.mkdtemp()
os.chdir(temp_dir)

# Download the video
print("Downloading video...")
try:
    subprocess.run(['yt-dlp', '--format', 'best', '--output', 'temp_video.%(ext)s', url], check=True)
except subprocess.CalledProcessError as e:
    print(f"Error downloading video: {e.stderr}")
    print("Press 'r' to retry or 'q' to quit.")
    while True:
        choice = input().strip().lower()
        if choice == 'r':
            # Retry download
            try:
                subprocess.run(['yt-dlp', '--format', 'best', '--output', 'temp_video.%(ext)s', url], check=True)
                break
            except subprocess.CalledProcessError as e2:
                print(f"Error downloading video: {e2.stderr}")
                print("Press 'r' to retry or 'q' to quit.")
        elif choice == 'q':
            sys.exit(1)

# Find the downloaded video file
video_files = glob.glob('temp_video.*')
if not video_files:
    print("Error: Downloaded video file not found.")
    sys.exit(1)
video_file = video_files[0]

# Process audio if selected
audio_file = None
if audio_format != 'none':
    audio_ext = audio_formats[audio_format]['ext']
    audio_file = f"output_audio.{audio_ext}"
    cmd = f'ffmpeg -i "{video_file}" -vn {audio_formats[audio_format]["command"]} "{audio_file}"'
    print(f"Extracting audio to {audio_file}...")
    try:
        subprocess.run(cmd, shell=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error extracting audio: {e}")
        sys.exit(1)

# Process video if selected
final_video_file = None
if video_format != 'none':
    video_ext = video_formats[video_format]['ext']
    final_video_file = f"output_video.{video_ext}"
    if video_formats[video_format]['command']:
        cmd = f'ffmpeg -i "{video_file}" {video_formats[video_format]["command"]} "{final_video_file}"'
        print(f"Converting video to {final_video_file}...")
        try:
            subprocess.run(cmd, shell=True, check=True)
        except subprocess.CalledProcessError as e:
            print(f"Error converting video: {e}")
            sys.exit(1)
    else:
        # If no command, just rename
        os.rename(video_file, final_video_file)

# Delete original video if no video format selected
if video_format == 'none':
    os.remove(video_file)

# Path completion setup
matches = []
def complete_path(text, state):
    global matches
    if state == 0:
        # Get the directory and base name
        if text.endswith('/'):
            dir_path = text
            base = ''
        else:
            dir_path = os.path.dirname(text) or '.'
            base = os.path.basename(text)
        try:
            # List files in the directory
            files = os.listdir(dir_path)
            # Filter files that start with base
            matches = [f for f in files if f.startswith(base)]
            # Make full paths
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

# Now, prompt for Jellyfin music library path
jellyfin_path = input("Please enter the full path to your Jellyfin music library: ")

# Validate the path
if not os.path.isdir(jellyfin_path):
    print("Error: The specified path is not a valid directory.")
    sys.exit(1)

# Import files into Jellyfin library
files_to_copy = []
if audio_file:
    files_to_copy.append(audio_file)
if final_video_file:
    files_to_copy.append(final_video_file)

for file in files_to_copy:
    dst = os.path.join(jellyfin_path, os.path.basename(file))
    try:
        os.link(file, dst)
        print(f"Hard-linked {file} to {dst}")
    except OSError as e:
        print(f"Error hard-linking {file}: {e}")

print("Files imported successfully into Jellyfin library.")

# Prompt to delete originals
delete_originals = input("Would you like to delete the original files? (y/n): ").strip().lower()
if delete_originals == 'y':
    for file in files_to_copy:
        try:
            os.remove(file)
            print(f"Deleted {file}")
        except OSError as e:
            print(f"Error deleting {file}: {e}")

print("Program completed.")