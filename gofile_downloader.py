"""
GoFile Downloader Module
Adapted for TrackUI integration - downloads files from gofile.io
"""

import os
import sys
import threading
from os import getcwd, makedirs, path, rmdir, listdir
from typing import Any, Iterator, Callable
from itertools import count
from requests import Session, Response, Timeout
from requests.structures import CaseInsensitiveDict
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from shutil import move
from time import perf_counter


class GoFileDownloader:
    """
    GoFile Downloader class to download files from gofile.io
    """
    
    def __init__(
        self,
        url: str,
        output_dir: str,
        password: str | None = None,
        max_workers: int = 5,
        number_retries: int = 5,
        timeout: float = 15.0,
        chunk_size: int = 2097152,  # 2MB chunks
        progress_callback: Callable[[str, int, int], None] | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        """
        Initialize GoFile Downloader
        
        :param url: GoFile URL to download from
        :param output_dir: Directory to save downloaded files
        :param password: Optional password for protected content
        :param max_workers: Maximum concurrent downloads
        :param number_retries: Number of retry attempts
        :param timeout: Request timeout in seconds
        :param chunk_size: Download chunk size
        :param progress_callback: Callback function(message, files_downloaded, total_files)
        :param stop_event: Threading event to stop download
        """
        self._url = url
        self._output_dir = output_dir
        self._password = password
        self._max_workers = max_workers
        self._number_retries = number_retries
        self._timeout = timeout
        self._chunk_size = chunk_size
        self._progress_callback = progress_callback
        self._stop_event = stop_event or threading.Event()
        
        # Session for HTTP requests
        self._session = Session()
        self._session.headers.update({
            "Accept-Encoding": "gzip",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Connection": "keep-alive",
            "Accept": "*/*",
        })
        
        # File tracking
        self._files_info: dict[str, dict[str, str]] = {}
        self._files_downloaded = 0
        self._total_files = 0
        self._errors: list[str] = []
    
    def _report_progress(self, message: str) -> None:
        """Report progress via callback"""
        if self._progress_callback:
            self._progress_callback(message, self._files_downloaded, self._total_files)
    
    def _get_response(self, **kwargs: Any) -> Response | None:
        """Make GET request with retries"""
        for _ in range(self._number_retries):
            try:
                return self._session.get(timeout=self._timeout, **kwargs)
            except Timeout:
                continue
            except Exception as e:
                self._errors.append(str(e))
                continue
        return None
    
    def _set_account_token(self, token: str | None = None) -> bool:
        """Set up GoFile account token for authentication"""
        if token:
            self._session.cookies.set("Cookie", f"accountToken={token}")
            self._session.headers.update({"Authorization": f"Bearer {token}"})
            return True
        
        # Create guest account
        for _ in range(self._number_retries):
            try:
                response = self._session.post(
                    "https://api.gofile.io/accounts",
                    timeout=self._timeout
                )
                data = response.json()
                
                if data.get("status") == "ok":
                    token = data["data"]["token"]
                    self._session.cookies.set("Cookie", f"accountToken={token}")
                    self._session.headers.update({"Authorization": f"Bearer {token}"})
                    return True
            except Timeout:
                continue
            except Exception as e:
                self._errors.append(f"Account creation failed: {e}")
                continue
        
        return False
    
    def _parse_content_id(self) -> str | None:
        """Extract content ID from GoFile URL"""
        try:
            parts = self._url.rstrip('/').split('/')
            if 'd' in parts:
                d_index = parts.index('d')
                if d_index + 1 < len(parts):
                    return parts[d_index + 1]
        except Exception:
            pass
        return None
    
    def _register_file(self, file_index: count, filepath: str, file_url: str) -> None:
        """Register a file for download"""
        self._files_info[str(next(file_index))] = {
            "path": path.dirname(filepath),
            "filename": path.basename(filepath),
            "link": file_url
        }
    
    @staticmethod
    def _resolve_naming_collision(
        pathing_count: dict[str, int],
        parent_dir: str,
        child_name: str,
        is_dir: bool = False,
    ) -> str:
        """Handle duplicate filenames"""
        filepath = path.join(parent_dir, child_name)
        
        if filepath in pathing_count:
            pathing_count[filepath] += 1
        else:
            pathing_count[filepath] = 0
        
        if pathing_count[filepath] > 0:
            if is_dir:
                return f"{filepath}({pathing_count[filepath]})"
            else:
                root, ext = path.splitext(filepath)
                return f"{root}({pathing_count[filepath]}){ext}"
        
        return filepath
    
    def _build_content_tree(
        self,
        parent_dir: str,
        content_id: str,
        password_hash: str | None = None,
        pathing_count: dict[str, int] | None = None,
        file_index: count = None
    ) -> None:
        """Build directory structure and register files"""
        if file_index is None:
            file_index = count(start=0, step=1)
        
        if pathing_count is None:
            pathing_count = {}
        
        url = f"https://api.gofile.io/contents/{content_id}?cache=true&sortField=createTime&sortDirection=1"
        
        if password_hash:
            url = f"{url}&password={password_hash}"
        
        response = self._get_response(url=url, headers={"X-Website-Token": "4fd6sg89d7s6"})
        
        if not response:
            self._errors.append(f"Failed to fetch content from {url}")
            return
        
        try:
            json_response = response.json()
        except Exception as e:
            self._errors.append(f"Failed to parse response: {e}")
            return
        
        if json_response.get("status") != "ok":
            self._errors.append(f"API error: {json_response.get('status')}")
            return
        
        data = json_response.get("data", {})
        
        # Check password protection
        if "password" in data and data.get("passwordStatus") != "passwordOk":
            self._errors.append("Content is password protected. Please provide the correct password.")
            return
        
        # Handle single file
        if data.get("type") != "folder":
            filepath = self._resolve_naming_collision(pathing_count, parent_dir, data.get("name", "unknown"))
            makedirs(path.dirname(filepath), exist_ok=True)
            self._register_file(file_index, filepath, data.get("link", ""))
            return
        
        # Handle folder
        folder_name = data.get("name", "unknown")
        absolute_path = self._resolve_naming_collision(pathing_count, parent_dir, folder_name, is_dir=True)
        
        # Use parent_dir if it ends with content_id (already the root)
        if path.basename(parent_dir) == content_id:
            absolute_path = parent_dir
        
        makedirs(absolute_path, exist_ok=True)
        
        # Process children
        children = data.get("children", {})
        for child in children.values():
            if self._stop_event.is_set():
                return
            
            if child.get("type") == "folder":
                self._build_content_tree(absolute_path, child["id"], password_hash, pathing_count, file_index)
            else:
                filepath = self._resolve_naming_collision(pathing_count, absolute_path, child.get("name", "unknown"))
                self._register_file(file_index, filepath, child.get("link", ""))
    
    def _download_file(self, file_info: dict[str, str]) -> bool:
        """Download a single file"""
        filepath = path.join(file_info["path"], file_info["filename"])
        
        # Skip if already exists
        if path.exists(filepath) and path.getsize(filepath) > 0:
            self._report_progress(f"Skipping {file_info['filename']} (exists)")
            self._files_downloaded += 1
            return True
        
        tmp_file = f"{filepath}.part"
        url = file_info["link"]
        
        for attempt in range(self._number_retries):
            if self._stop_event.is_set():
                return False
            
            try:
                headers = {}
                part_size = 0
                
                if path.isfile(tmp_file):
                    part_size = int(path.getsize(tmp_file))
                    headers = {"Range": f"bytes={part_size}-"}
                
                response = self._get_response(url=url, headers=headers, stream=True)
                
                if not response:
                    continue
                
                status_code = response.status_code
                
                # Validate response
                if status_code in (403, 404, 405, 500):
                    self._errors.append(f"HTTP {status_code} for {file_info['filename']}")
                    return False
                
                if part_size == 0 and status_code not in (200, 206):
                    continue
                if part_size > 0 and status_code != 206:
                    continue
                
                # Get total file size
                content_length = response.headers.get("Content-Length")
                content_range = response.headers.get("Content-Range")
                
                if part_size == 0:
                    total_size = int(content_length) if content_length else 0
                elif content_range:
                    total_size = int(content_range.split("/")[-1])
                else:
                    total_size = 0
                
                if not total_size:
                    self._errors.append(f"Could not determine size for {file_info['filename']}")
                    return False
                
                # Download with progress
                makedirs(file_info["path"], exist_ok=True)
                
                with open(tmp_file, "ab") as f:
                    downloaded = part_size
                    start_time = perf_counter()
                    
                    for chunk in response.iter_content(chunk_size=self._chunk_size):
                        if self._stop_event.is_set():
                            return False
                        
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        # Calculate progress
                        progress = (downloaded / total_size) * 100 if total_size else 0
                        elapsed = perf_counter() - start_time
                        rate = (downloaded - part_size) / elapsed if elapsed > 0 else 0
                        
                        # Format rate
                        if rate < 1024:
                            rate_str = f"{rate:.1f} B/s"
                        elif rate < 1024 ** 2:
                            rate_str = f"{rate / 1024:.1f} KB/s"
                        elif rate < 1024 ** 3:
                            rate_str = f"{rate / (1024 ** 2):.1f} MB/s"
                        else:
                            rate_str = f"{rate / (1024 ** 3):.1f} GB/s"
                        
                        self._report_progress(
                            f"Downloading {file_info['filename']}: {progress:.1f}% @ {rate_str}"
                        )
                
                # Finalize download
                if path.getsize(tmp_file) == total_size:
                    move(tmp_file, filepath)
                    self._files_downloaded += 1
                    self._report_progress(f"Completed: {file_info['filename']}")
                    return True
                else:
                    # Incomplete download, retry
                    continue
                
            except Timeout:
                continue
            except Exception as e:
                self._errors.append(f"Error downloading {file_info['filename']}: {e}")
                continue
        
        return False
    
    def download(self) -> dict[str, Any]:
        """
        Execute the download
        
        Returns dict with:
        - success: bool
        - files_downloaded: int
        - total_files: int
        - errors: list[str]
        - message: str
        """
        self._report_progress("Initializing GoFile download...")
        
        # Parse content ID
        content_id = self._parse_content_id()
        if not content_id:
            return {
                "success": False,
                "files_downloaded": 0,
                "total_files": 0,
                "errors": ["Invalid GoFile URL - could not extract content ID"],
                "message": "Invalid GoFile URL"
            }
        
        # Set up authentication
        self._report_progress("Setting up GoFile authentication...")
        if not self._set_account_token():
            return {
                "success": False,
                "files_downloaded": 0,
                "total_files": 0,
                "errors": self._errors or ["Failed to create GoFile account"],
                "message": "Authentication failed"
            }
        
        # Hash password if provided
        password_hash = None
        if self._password:
            password_hash = sha256(self._password.encode()).hexdigest()
        
        # Build content tree structure
        self._report_progress("Fetching content structure...")
        content_dir = path.join(self._output_dir, content_id)
        self._build_content_tree(content_dir, content_id, password_hash)
        
        # Check if we found any files
        if not self._files_info:
            # Try to clean up empty directory
            try:
                if path.exists(content_dir) and not listdir(content_dir):
                    rmdir(content_dir)
            except:
                pass
            
            if self._errors:
                return {
                    "success": False,
                    "files_downloaded": 0,
                    "total_files": 0,
                    "errors": self._errors,
                    "message": self._errors[-1] if self._errors else "No files found"
                }
            else:
                return {
                    "success": True,
                    "files_downloaded": 0,
                    "total_files": 0,
                    "errors": [],
                    "message": "No files found in content"
                }
        
        self._total_files = len(self._files_info)
        self._report_progress(f"Found {self._total_files} files, starting download...")
        
        # Download files concurrently
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            for file_info in self._files_info.values():
                if self._stop_event.is_set():
                    break
                executor.submit(self._download_file, file_info)
        
        # Return results
        success = self._files_downloaded > 0 or not self._errors
        
        return {
            "success": success,
            "files_downloaded": self._files_downloaded,
            "total_files": self._total_files,
            "errors": self._errors,
            "message": f"Downloaded {self._files_downloaded}/{self._total_files} files"
        }


def download_gofile(
    url: str,
    output_dir: str,
    password: str | None = None,
    progress_callback: Callable[[str, int, int], None] | None = None,
    stop_event: threading.Event | None = None,
) -> dict[str, Any]:
    """
    Convenience function to download from GoFile
    
    :param url: GoFile URL
    :param output_dir: Output directory
    :param password: Optional password
    :param progress_callback: Callback function(message, files_downloaded, total_files)
    :param stop_event: Event to stop download
    :return: Result dictionary
    """
    downloader = GoFileDownloader(
        url=url,
        output_dir=output_dir,
        password=password,
        progress_callback=progress_callback,
        stop_event=stop_event,
    )
    return downloader.download()


def is_gofile_url(url: str) -> bool:
    """Check if a URL is a GoFile URL"""
    return "gofile.io/d/" in url.lower()
