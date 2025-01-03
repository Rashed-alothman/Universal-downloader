import sys
import os
import yt_dlp
import argparse
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional, List, Dict
import json
import textwrap
import mutagen
from mutagen.mp3 import MP3
from mutagen.flac import FLAC
import subprocess
from tqdm import tqdm
import threading
import time
from colorama import init, Fore, Style

# Initialize colorama for Windows support
init()

class CustomHelpFormatter(argparse.HelpFormatter):
    def _split_lines(self, text, width):
        return [line for line in textwrap.wrap(text, width)]

EXAMPLES = """
Examples:
---------
1. Download a video from various platforms:
   python UniversalDownloader.py "https://youtube.com/watch?v=example"
   python UniversalDownloader.py "https://vimeo.com/123456789"
   python UniversalDownloader.py "https://dailymotion.com/video/x7tgd2g"
   python UniversalDownloader.py "https://twitch.tv/videos/1234567890"

2. Download audio only (MP3):
   python UniversalDownloader.py "https://soundcloud.com/artist/track" --audio-only

3. Download video in specific resolution:
   python UniversalDownloader.py "https://youtube.com/watch?v=example" --resolution 1080

4. Download multiple videos:
   python UniversalDownloader.py "URL1" "URL2" "URL3"

5. Download with custom filename:
   python UniversalDownloader.py "URL" --filename "my_video"

6. Download audio in FLAC format:
   python UniversalDownloader.py "URL" --audio-only --audio-format flac

Advanced Usage:
--------------
- Custom output directory:
  python UniversalDownloader.py "URL" --output-dir "path/to/directory"

- Specify format ID (for advanced users):
  python UniversalDownloader.py "URL" --format-id 137+140

- List all supported sites:
  python UniversalDownloader.py --list-sites

Common Issues:
-------------
1. FFMPEG not found: Install FFMPEG and update config.json
2. SSL Error: Update Python and yt-dlp
3. Permission Error: Run with admin privileges
"""

CONFIG_FILE = 'config.json'
DEFAULT_CONFIG = {
    'ffmpeg_location': '',  # Will be auto-detected
    'video_output': str(Path.home() / 'Videos'),
    'audio_output': str(Path.home() / 'Music'),
    'max_concurrent': 3
}

def find_ffmpeg():
    """Find FFmpeg in common locations or PATH"""
    common_locations = [
        r'C:\ffmpeg\bin',
        r'C:\Program Files\ffmpeg\bin',
        r'C:\ffmpeg\ffmpeg-master-latest-win64-gpl\bin',
        r'.\ffmpeg\bin',  # Relative to script location
    ]
    
    # Check if ffmpeg is in PATH
    if os.system('ffmpeg -version > nul 2>&1') == 0:
        return 'ffmpeg'
    
    # Check common locations
    for location in common_locations:
        ffmpeg_path = os.path.join(location, 'ffmpeg.exe')
        if os.path.exists(ffmpeg_path):
            return location
    
    return None

def print_ffmpeg_instructions():
    """Print instructions for installing FFmpeg"""
    print(f"{Fore.YELLOW}FFmpeg not found! Please follow these steps to install FFmpeg:{Style.RESET_ALL}")
    print("\n1. Download FFmpeg:")
    print("   - Visit: https://github.com/BtbN/FFmpeg-Builds/releases")
    print("   - Download: ffmpeg-master-latest-win64-gpl.zip")
    print("\n2. Install FFmpeg:")
    print("   - Extract the downloaded zip file")
    print("   - Copy the extracted folder to C:\\ffmpeg")
    print("   - Ensure ffmpeg.exe is in C:\\ffmpeg\\bin")
    print("\nAlternatively:")
    print("- Use chocolatey: choco install ffmpeg")
    print("- Use winget: winget install ffmpeg")
    print("\nAfter installation, either:")
    print("1. Add FFmpeg to your system PATH, or")
    print("2. Update config.json with the correct ffmpeg_location")
    print("\nFor detailed instructions, visit: https://www.wikihow.com/Install-FFmpeg-on-Windows")

class ColorProgressBar:
    def __init__(self, total, desc="Processing"):
        self.progress = tqdm(
            total=total,
            desc=f"{Fore.CYAN}{desc}{Style.RESET_ALL}",
            bar_format="{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt}",
            ncols=80,
            unit="%"
        )
        self.colors = [Fore.BLUE, Fore.CYAN, Fore.GREEN, Fore.YELLOW]
        self.current_color_idx = 0
        self.last_update = 0

    def update(self, n=1):
        current_time = time.time()
        if current_time - self.last_update > 0.1:  # Limit color updates
            self.current_color_idx = (self.current_color_idx + 1) % len(self.colors)
            self.progress.bar_format = (
                "{desc}: {percentage:3.0f}%|"
                f"{self.colors[self.current_color_idx]}"
                "{bar}"
                f"{Style.RESET_ALL}"
                "| {n_fmt}/{total_fmt}"
            )
            self.last_update = current_time
        self.progress.update(n)

    def close(self):
        self.progress.close()
        print(f"\n{Fore.GREEN}✓ Complete!{Style.RESET_ALL}")

class DownloadManager:
    def __init__(self, config: Dict):
        self.config = config
        if not self.config['ffmpeg_location']:
            ffmpeg_path = find_ffmpeg()
            if ffmpeg_path:
                self.config['ffmpeg_location'] = ffmpeg_path
            else:
                print_ffmpeg_instructions()
                raise FileNotFoundError("FFmpeg is required but not found. Please install FFmpeg and try again.")
        
        self.setup_logging()
        self.verify_paths()
        self.last_percentage = 0  # Initialize last_percentage

    def setup_logging(self):
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('download_log.txt'),
                logging.StreamHandler()
            ]
        )

    def verify_paths(self):
        if not os.path.exists(self.config['ffmpeg_location']):
            raise FileNotFoundError(f"FFMPEG not found at {self.config['ffmpeg_location']}")
        os.makedirs(self.config['video_output'], exist_ok=True)
        os.makedirs(self.config['audio_output'], exist_ok=True)

    def progress_hook(self, d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            downloaded = d.get('downloaded_bytes', 0)
            
            # Initialize progress bar if not exists
            if not hasattr(self, 'pbar'):
                self.pbar = ColorProgressBar(100, desc="Downloading")
            
            if total > 0:
                percentage = min(int((downloaded / total) * 100), 100)
                if percentage > self.last_percentage:
                    self.pbar.update(percentage - self.last_percentage)
                    self.last_percentage = percentage
                
        elif d['status'] == 'finished':
            if hasattr(self, 'pbar'):
                self.pbar.close()
                delattr(self, 'pbar')
            self.last_percentage = 0  # Reset last_percentage
            print(f"{Fore.GREEN}✓ Download Complete!{Style.RESET_ALL}")

    def verify_audio_file(self, filepath: str) -> bool:
        """Enhanced audio file verification"""
        try:
            if filepath.lower().endswith('.flac'):
                audio = FLAC(filepath)
                # Additional FLAC-specific checks
                if not audio.tags:
                    # Initialize tags if missing
                    audio.tags = mutagen.flac.VorbisComment()
                    audio.save()
                
                # Verify FLAC properties
                if audio.info.channels not in (1, 2):
                    logging.error(f"Unexpected channel count: {audio.info.channels}")
                    return False
                
                if audio.info.sample_rate not in (44100, 48000, 96000):
                    logging.error(f"Unexpected sample rate: {audio.info.sample_rate}")
                    return False
                
                # Check file size (should be reasonable for FLAC)
                file_size = os.path.getsize(filepath)
                if file_size < 1024 * 100:  # Less than 100KB is suspicious
                    logging.error(f"FLAC file too small: {file_size} bytes")
                    return False
            
            # ...rest of existing verify_audio_file code...

        except Exception as e:
            logging.error(f"Error verifying audio file {filepath}: {str(e)}")
            return False

    def convert_to_flac(self, input_file: str, output_file: str) -> bool:
        """Convert audio to FLAC with high quality settings and metadata preservation."""
        try:
            # First verify the input file
            if not self.verify_audio_file(input_file):
                raise ValueError("Input file verification failed")

            # Get original metadata and file size
            original_audio = mutagen.File(input_file)
            input_size = os.path.getsize(input_file)
            
            # Prepare FFmpeg command with progress pipe
            cmd = [
                os.path.join(self.config['ffmpeg_location'], 'ffmpeg'),
                '-i', input_file,
                '-c:a', 'flac',
                '-compression_level', '12',
                '-sample_fmt', 's32',
                '-ar', '48000',
                '-progress', 'pipe:1',
                output_file
            ]
            
            # Create progress bar
            pbar = ColorProgressBar(100, desc="Converting to FLAC")
            
            # Execute conversion with progress monitoring
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True
            )

            # Monitor conversion progress
            last_progress = 0
            while True:
                line = process.stdout.readline()
                if not line and process.poll() is not None:
                    break
                
                if 'out_time_ms=' in line:
                    try:
                        time_ms = int(line.split('=')[1])
                        progress = min(int((time_ms / 1000) / original_audio.info.length * 100), 100)
                        if progress > last_progress:
                            pbar.update(progress - last_progress)
                            last_progress = progress
                    except (ValueError, AttributeError):
                        continue

            # Close progress bar
            pbar.close()

            # Check conversion result
            if process.returncode != 0:
                raise RuntimeError(f"FFmpeg conversion failed: {process.stderr.read()}")

            # Verify and handle metadata
            if not self.verify_audio_file(output_file):
                raise ValueError("Output file verification failed")

            if original_audio and original_audio.tags:
                flac_audio = FLAC(output_file)
                for key, value in original_audio.tags.items():
                    flac_audio[key] = value
                flac_audio.save()

            # Compare files
            orig_info = mutagen.File(input_file).info
            conv_info = mutagen.File(output_file).info
            
            if abs(orig_info.length - conv_info.length) > 0.1:  # Allow 100ms difference
                raise ValueError("Duration mismatch between input and output files")

            return True

        except Exception as e:
            print(f"\n{Fore.RED}✗ Conversion Failed: {str(e)}{Style.RESET_ALL}")
            if os.path.exists(output_file):
                os.remove(output_file)
            return False

    def get_download_options(self, url: str, audio_only: bool, resolution: Optional[str] = None,
                           format_id: Optional[str] = None, filename: Optional[str] = None,
                           audio_format: str = 'mp3') -> Dict:
        output_path = self.config['audio_output'] if audio_only else self.config['video_output']
        
        # Add TikTok-specific options
        if 'tiktok.com' in url:
            options = {
                'format': 'best',  # Use best available format for TikTok
                'outtmpl': os.path.join(output_path, f"{filename or '%(title)s'}.%(ext)s"),
                'ffmpeg_location': self.config['ffmpeg_location'],
                'progress_hooks': [self.progress_hook],
                'ignoreerrors': True,
                'continue': True,
                'postprocessor_hooks': [self.post_process_hook],
                'concurrent_fragment_downloads': 3,
                'cookiefile': 'cookies.txt',  # Add cookie file support
                'extractor_args': {
                    'tiktok': {
                        'download_timeout': 30,  # Increase timeout
                        'extract_flat': False,
                        'force_generic_extractor': False
                    }
                }
            }
            return options

        # Original options for other platforms
        if audio_only:
            format_option = 'bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio'
        elif resolution:
            format_option = f'bestvideo[height<={resolution}]+bestaudio/best[height<={resolution}]/best'
        else:
            format_option = 'bestvideo+bestaudio/best'
        
        options = {
            'format': format_option,
            'outtmpl': os.path.join(output_path, f"{filename or '%(title)s'}.%(ext)s"),
            'ffmpeg_location': self.config['ffmpeg_location'],
            'progress_hooks': [self.progress_hook],
            'ignoreerrors': True,
            'continue': True,
            'postprocessor_hooks': [self.post_process_hook],
            'concurrent_fragment_downloads': 3,
        }

        if audio_only:
            if audio_format == 'flac':
                options['postprocessors'] = [
                    {
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'flac',
                        'preferredquality': '0',
                    },
                    {
                        'key': 'FFmpegMetadata',
                        'add_metadata': True,
                    }
                ]
                # Direct FLAC conversion settings
                options['extract_audio'] = True
                options['audio_quality'] = 0
                options['audio_format'] = 'flac'
            else:
                options['postprocessors'] = [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': audio_format,
                    'preferredquality': '320' if audio_format == 'mp3' else '0',
                }]

        if format_id:
            options['format'] = format_id

        return options

    def post_process_hook(self, d):
        """Handle post-processing events"""
        if d['status'] == 'started':
            print(f"\n{Fore.CYAN}Post-processing: {d.get('info_dict', {}).get('title', 'Unknown')}{Style.RESET_ALL}")
        elif d['status'] == 'finished':
            filename = d.get('filename', '')
            if filename.endswith('.flac'):
                # Verify FLAC file
                if self.verify_audio_file(filename):
                    print(f"{Fore.GREEN}✓ FLAC conversion successful: {os.path.basename(filename)}{Style.RESET_ALL}")
                else:
                    print(f"{Fore.RED}✗ FLAC verification failed: {os.path.basename(filename)}{Style.RESET_ALL}")

    def download(self, url: str, **kwargs):
        try:
            print(f"\n{Fore.CYAN}Fetching video information...{Style.RESET_ALL}")
            
            # Show download type and format
            if kwargs.get('audio_only'):
                format_name = kwargs.get('audio_format', 'mp3').upper()
                print(f"{Fore.YELLOW}Mode: Audio Only ({format_name}){Style.RESET_ALL}")
            else:
                resolution = kwargs.get('resolution', 'best')
                print(f"{Fore.YELLOW}Mode: Video (Quality: {resolution}){Style.RESET_ALL}")

            # Add browser headers for TikTok
            if 'tiktok.com' in url:
                yt_dlp.utils.std_headers.update({
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Language': 'en-us,en;q=0.5',
                    'Sec-Fetch-Mode': 'navigate',
                })
            
            # Reset progress bar state
            if hasattr(self, 'pbar'):
                delattr(self, 'pbar')
            self.last_percentage = 0
            
            with yt_dlp.YoutubeDL(self.get_download_options(url, **kwargs)) as ydl:
                # First get info to check availability
                try:
                    info = ydl.extract_info(url, download=False)
                    if not info:
                        raise ValueError("Could not fetch video information")
                    
                    if kwargs.get('audio_only'):
                        print(f"{Fore.CYAN}Found audio: {info.get('title', 'Unknown')}{Style.RESET_ALL}")
                        print(f"Format: {info.get('format', 'Unknown')}")
                    
                    # Proceed with download
                    ydl.download([url])
                    
                except yt_dlp.utils.DownloadError as e:
                    print(f"{Fore.RED}Download Error: {str(e)}{Style.RESET_ALL}")
                    print("Try updating yt-dlp: pip install -U yt-dlp")
                    return False
                
                except Exception as e:
                    print(f"{Fore.RED}Error: {str(e)}{Style.RESET_ALL}")
                    return False
            
            return True
            
        except Exception as e:
            logging.error(f"Error downloading {url}: {str(e)}")
            return False

    def batch_download(self, urls: List[str], **kwargs):
        with ThreadPoolExecutor(max_workers=self.config['max_concurrent']) as executor:
            futures = [executor.submit(self.download, url, **kwargs) for url in urls]
            return [f.result() for f in futures]

    def list_supported_sites(self):
        with yt_dlp.YoutubeDL() as ydl:
            print("Supported Sites:")
            print("---------------")
            for extractor in ydl._ies:
                if extractor._VALID_URL:
                    print(f"- {extractor.IE_NAME}")

    def show_menu(self):
        """Display available options menu"""
        print(f"\n{Fore.CYAN}Available Options:{Style.RESET_ALL}")
        print("1. Just paste URL to download in highest quality")
        print("\n2. Audio Options:")
        print(f"   {Fore.YELLOW}--audio-only{Style.RESET_ALL}                    Download audio only")
        print(f"   {Fore.YELLOW}--audio-format [format]{Style.RESET_ALL}         Choose audio format:")
        print("      Available formats:")
        print(f"      {Fore.GREEN}mp3{Style.RESET_ALL}  - Standard MP3 format (320kbps)")
        print(f"      {Fore.GREEN}flac{Style.RESET_ALL} - Lossless audio format")
        print(f"      {Fore.GREEN}wav{Style.RESET_ALL}  - Uncompressed audio")
        print(f"      {Fore.GREEN}m4a{Style.RESET_ALL}  - AAC audio format")
        
        print("\n3. Video Options:")
        print(f"   {Fore.YELLOW}--resolution [quality]{Style.RESET_ALL}          Set video quality (e.g., 720, 1080, 2160)")

        print("\nExamples:")
        print("- Download video: just paste the URL")
        print("- MP3 audio: URL --audio-only")
        print("- FLAC audio: URL --audio-only --audio-format flac")
        print("- HD video: URL --resolution 1080")
        print(f"\nType {Fore.CYAN}--help{Style.RESET_ALL} to show this menu")
        print(f"Type {Fore.RED}--Q{Style.RESET_ALL} to quit\n")

    def interactive_mode(self):
        print(f"\n{Fore.CYAN}Welcome to Universal Downloader!{Style.RESET_ALL}")
        self.show_menu()
        
        while True:
            try:
                user_input = input(f"\n{Fore.GREEN}Enter URL and options:{Style.RESET_ALL} ").strip()
                
                if user_input.lower() == '--q':
                    print(f"{Fore.YELLOW}Exiting...{Style.RESET_ALL}")
                    break
                
                if not user_input:
                    continue
                
                if user_input.lower() == '--help':
                    self.show_menu()
                    continue

                # Parse user input
                args = user_input.split()
                url = args[0]
                options = {
                    'audio_only': '--audio-only' in args,
                    'resolution': None,
                    'audio_format': 'mp3'
                }

                # Parse resolution
                if '--resolution' in args:
                    try:
                        res_index = args.index('--resolution')
                        options['resolution'] = args[res_index + 1]
                    except (ValueError, IndexError):
                        pass

                # Parse audio format
                if '--audio-format' in args:
                    try:
                        format_index = args.index('--audio-format')
                        options['audio_format'] = args[format_index + 1]
                    except (ValueError, IndexError):
                        pass

                # Download with options
                self.download(url, **options)
                
            except KeyboardInterrupt:
                print(f"\n{Fore.YELLOW}Exiting...{Style.RESET_ALL}")
                break
            except Exception as e:
                print(f"{Fore.RED}Error: {str(e)}{Style.RESET_ALL}")
                print("Type --help to see available options")

def load_config() -> Dict:
    try:
        with open(CONFIG_FILE, 'r') as f:
            return {**DEFAULT_CONFIG, **json.load(f)}
    except FileNotFoundError:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(DEFAULT_CONFIG, f, indent=4)
        return DEFAULT_CONFIG

def main():
    # If no arguments provided, run in interactive mode directly
    if len(sys.argv) == 1:
        config = load_config()
        manager = DownloadManager(config)
        manager.interactive_mode()
        sys.exit(0)
        
    # Rest of argument parsing for advanced usage
    parser = argparse.ArgumentParser(
        description="Universal Downloader - Download videos and audio from hundreds of websites including YouTube, Vimeo, Twitter, TikTok, Instagram, Twitch, and more!",
        formatter_class=CustomHelpFormatter,
        epilog=EXAMPLES,
    )

    # URL input group (mutually exclusive)
    url_group = parser.add_mutually_exclusive_group(required=True)
    url_group.add_argument(
        '--url',
        help="URL to download"
    )
    url_group.add_argument(
        'urls',
        nargs='*',
        help="One or more URLs to download (space-separated)",
        default=[]
    )

    # Add list-sites option
    parser.add_argument(
        '--list-sites',
        action='store_true',
        help="List all supported sites"
    )
    
    # Required arguments group
    required = parser.add_argument_group('Required Arguments')

    # Download options group
    download_opts = parser.add_argument_group('Download Options')
    download_opts.add_argument(
        '--resolution',
        help="Maximum video resolution (e.g., 720, 1080). If not specified, downloads best quality available",
        metavar="RES"
    )
    download_opts.add_argument(
        '--audio-only',
        action='store_true',
        help="Download audio only (default format: MP3)"
    )
    download_opts.add_argument(
        '--audio-format',
        default='mp3',
        choices=['mp3', 'flac', 'wav', 'm4a'],
        help="Audio format when using --audio-only"
    )

    # Advanced options group
    advanced = parser.add_argument_group('Advanced Options')
    advanced.add_argument(
        '--format-id',
        help="Specific format ID for download (advanced users)",
        metavar="FMT"
    )
    advanced.add_argument(
        '--filename',
        help="Custom filename without extension",
        metavar="NAME"
    )
    advanced.add_argument(
        '--output-dir',
        help="Custom output directory path",
        metavar="DIR"
    )

    # Add version info
    parser.add_argument(
        '--version',
        action='version',
        version='Universal Downloader v1.0.0'
    )

    # Add interactive mode option
    parser.add_argument(
        '--interactive',
        action='store_true',
        help="Run in interactive mode (enter URLs one by one, --Q to quit)"
    )

    args = parser.parse_args()

    # Show supported sites if requested
    if args.list_sites:
        DownloadManager(load_config()).list_supported_sites()
        sys.exit(0)

    # Show full help if no arguments provided
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)

    config = load_config()
    
    if args.output_dir:
        config['video_output'] = args.output_dir
        config['audio_output'] = args.output_dir

    manager = DownloadManager(config)

    # Handle interactive mode
    if args.interactive:
        manager.interactive_mode()
        sys.exit(0)

    # Combine --url and positional urls
    all_urls = [args.url] if args.url else args.urls
    
    if len(all_urls) == 1:
        success = manager.download(
            all_urls[0],
            audio_only=args.audio_only,
            resolution=args.resolution,
            format_id=args.format_id,
            filename=args.filename,
            audio_format=args.audio_format
        )
    else:
        successes = manager.batch_download(
            all_urls,
            audio_only=args.audio_only,
            resolution=args.resolution,
            format_id=args.format_id,
            audio_format=args.audio_format
        )
        success = all(successes)

    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
