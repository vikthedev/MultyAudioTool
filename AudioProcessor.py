import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, List, Dict, Union, Any

from AudioInfo import AudioInfo, AudioFormat
from Utils import Utils


class AudioProcessor:
    """
    AudioProcessor handles TrueHD/AC3 → PCM decoding via GStreamer,
    channel selection, and final encoding with SoX + FFmpeg.
    """

    def __init__(self,
                 gst_launch: Path,
                 sox_launch: Path,
                 ffmpeg_launch: Path,
                 eac3to_launch: Path,
                 mediainfo_launch: Path,
                 channels: Dict
                 ):

        """
        Initialize audio processor paths and files.
        Args:
            gst_launch: Path to gst-launch-1.0 executable
            sox_launch: Path to SoX executable
            ffmpeg_launch: Path to FFmpeg executable
            eac3to_launch: Path to FFmpeg executable
            mediainfo_launch: Path to FFmpeg executable
            channels: Dict with 'id' and 'names'
        """
        self.THD = AudioFormat.TRUEHD
        self.AC3 = AudioFormat.AC3

        self.GST_DELAY_THD = 32  # GStreamer adds 32 samples at start at True HD decoding
        self.GST_DELAY_AC3 = -224  # GStreamer removes 224 samples at start at E-AC3 decoding

        self.gst_launch: Path = gst_launch
        self.sox_launch: Path = sox_launch
        self.ffmpeg_launch: Path = ffmpeg_launch
        self.mediainfo_launch: Path = mediainfo_launch
        self.eac3to_launch: Path = eac3to_launch

        self.channels_config_id: Dict = channels.get('id')
        self.channels_layout: List = channels.get('names')
        self.channels_count: int = len(channels.get('names'))

    def get_delay_fix(self, input_format: str) -> int:
        return self.GST_DELAY_THD if input_format == self.THD else self.GST_DELAY_AC3

    def run(self,
            input_file: Path,
            output_file: Path,
            keep_raw: bool = True,
            no_numbers: bool = False,
            bits: int = 24,
            delay: int = 0,
            volume: float = 0,
            duration: float = 0,
            channels_filter: List[str] = []
            ) -> int:

        params = locals().copy()
        params.pop("self")
        # Utils.Console.cls()
        code = self._Runner(self, **params).run()
        return code

    # ---------------------------
    # Private Processor
    # ---------------------------
    class _Runner:
        def __init__(self, parent: "AudioProcessor", **kwargs):
            self.parent: AudioProcessor = parent
            self.input_file: Path = kwargs.get('input_file')
            self.output_file: Path = kwargs.get('output_file').with_suffix(".wav")
            self.temp_raw_file: Path = kwargs.get('output_file').with_suffix(".raw")
            self.temp_raw_info_file: Path = kwargs.get('output_file').with_suffix(".txt")

            self.input_frequency: int = 48000
            self.no_numbers: bool = kwargs.get('no_numbers')
            self.keep_raw: bool = kwargs.get('keep_raw')
            self.bits: int = kwargs.get('bits')

            self.channels_filter: List[str] = kwargs.get('channels_filter')

            # Set/get by methods
            self._duration: Union[int, float] = Utils.Format.to_float(kwargs.get('duration', 0))
            self._delay: int = Utils.Format.to_int(kwargs.get('delay', 0))
            self._volume: float = Utils.Format.to_float(kwargs.get('volume'))

            self.audio_info: Optional[Dict[str, Union[str, int, None]]] = {
                "format": None,
                "duration": None,
                "channels": None,
                "freq": None,
                "dialnorm": None,
                "parser_used": None
            }
            self._output_files: Optional[List[Path]] = None

        @property
        def delay(self) -> int:
            return getattr(self, "_delay", 0) - self.parent.get_delay_fix(self.input_format)

        @delay.setter
        def delay(self, key: int) -> None:
            """ fix GST initial THD/E-AC3 delay """
            self._delay = key

        @property
        def input_format(self) -> Optional[str]:
            fmt = self.get_audio_info("format")
            fmt = fmt.upper() if isinstance(fmt, str) else None
            return self.parent.THD if fmt == AudioFormat.TRUEHD else self.parent.AC3 if fmt == AudioFormat.AC3 else None

        @property
        def duration(self) -> Optional[Union[int, float]]:
            """Return best available duration in seconds."""
            parsed_duration = self.get_audio_info("duration")
            stored_duration = getattr(self, "_duration", None)
            return stored_duration if stored_duration and (not parsed_duration or stored_duration < parsed_duration) \
                else parsed_duration

        @duration.setter
        def duration(self, key: Union[int, float]) -> None:
            """ fix GST initial THD/E-AC3 delay """
            self._duration = key

        @property
        def volume(self) -> Union[int, float]:
            dialnorm = self.get_audio_info("dialnorm")
            volume = getattr(self, "_volume", 0)
            if volume is None:
                if dialnorm is None:
                    volume = 0
                else:
                    volume = 31 + dialnorm
            return volume

        @volume.setter
        def volume(self, key: Union[int, float]) -> None:
            """ fix GST initial THD/E-AC3 delay """
            self._volume = key

        def run(self) -> int:
            if not self.input_file.exists() and not self.temp_raw_file.exists:
                Utils.Console.cprint(f'\nFile not found:\n {self.input_file}', 'red')
                return 1

            self.prepare_audio_info()

            # Run GStreamer first
            code = self.run_gstreamer()

            if code == 0:
                # Run SoX → FFmpeg only if GStreamer succeeded
                code = self.run_sox_ffmpeg()
            if code == 0 and not self.keep_raw:
                print(f'\nRemoving raw file\n')
                Utils.IO.delete_files([self.temp_raw_file, self.temp_raw_info_file])
            return code

        # ------------------ GStreamer Processor ------------------
        def run_gstreamer(self) -> int:
            """
            Run GStreamer TrueHD/EAC3 → PCM with live progress output.
            Returns:
                int: GStreamer process return code
            """

            print("\nGStreamer started...\n")

            total_duration = self.get_audio_info("duration")

            if self.temp_raw_file.exists():
                Utils.update_progress_bar(start_time=time.time(), percent_done=100,
                                          seconds_passed=total_duration, total_duration=total_duration)
                sys.stdout.write("\n")
                print("\nGStreamer finished successfully.")
                return 0

            if self.input_format == self.parent.THD:
                gst_cmd = self._build_gstreamer_thd_command()
            elif self.input_format == self.parent.AC3:
                gst_cmd = self._build_gstreamer_ac3_command()
            else:
                raise ValueError(Utils.Format.colorize(f"Unsupported input_format: {self.input_format}", 'red'))

            seconds_passed = None

            try:
                proc = subprocess.Popen(
                    gst_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    universal_newlines=True,
                    bufsize=1,
                    shell=False
                )
                start_time = time.time()
                stderr: list = []
                while True:
                    line = proc.stdout.readline()
                    if not line and proc.poll() is not None:
                        break
                    if line:
                        if (_sp := self._parse_gstreamer_output_line(
                                line=line.strip(), start_time=start_time, total_duration=total_duration)) is not None:
                            seconds_passed = _sp
                        else:
                            stderr.append(line)
            except KeyboardInterrupt:
                Utils.Proc.handle_interrupt(proc, process_name="GStreamer")
                Utils.IO.delete_files([self.temp_raw_file, self.temp_raw_info_file])
                return 1

            proc.wait()
            sys.stdout.write("\n")
            if proc.returncode == 0:
                # Save track length for FFmpeg progress (non-critical)
                self.audio_info["duration"] = seconds_passed
                self._put_raw_audio_info(self.audio_info)
                print("\nGStreamer finished successfully.")
            else:
                Utils.IO.delete_files([self.temp_raw_file, self.temp_raw_info_file])
                Utils.Console.cprint(f"GStreamer failed with code {proc.returncode}", 'red')
                Utils.Console.cprint(''.join(stderr), 'darkgray')
            return proc.returncode

        # ------------------ SoX / FFmpeg  Processor ------------------
        def run_sox_ffmpeg(self) -> int:
            """
            Convert intermediate PCM with SoX and encode selected channels with FFmpeg.
            Shows live progress based on FFmpeg stderr, optionally with progress bar if total duration is known.
            """
            sox_cmd = self._build_sox_command(self.bits, self.delay, self.volume)
            ffmpeg_cmd = self._build_ffmpeg_command(self.bits, self.no_numbers, self.duration, self.channels_filter)

            print("\nFFmpeg started...\n")
            try:
                sox_proc = subprocess.Popen(
                    sox_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    bufsize=0,
                    shell=False
                )

                ffmpeg_proc = subprocess.Popen(
                    ffmpeg_cmd,
                    stdin=sox_proc.stdout,
                    stderr=subprocess.PIPE,
                    bufsize=1,
                    universal_newlines=True,
                    shell=False
                )

                sox_proc.stdout.close()
                start_time = time.time()
                stderr: list = []
                seconds_passed = None
                while True:
                    line = ffmpeg_proc.stderr.readline()
                    if not line and ffmpeg_proc.poll() is not None:
                        break
                    if line:
                        if (_sp := self._parse_ffmpeg_output_line(
                                line=line, start_time=start_time, total_duration=self.duration)) is not None:
                            seconds_passed = _sp
                        else:
                            stderr.append(line)

                # bug fix in duration log in FFMPEG 6.x
                if seconds_passed and seconds_passed < self.duration:
                    self._parse_ffmpeg_output_line(
                        line=f"time={Utils.Format.to_human_time(self.duration)}.000", start_time=start_time, total_duration=self.duration)

                sys.stdout.write("\n")
                sys.stdout.flush()
                ffmpeg_proc.wait()
                sox_proc.wait()

                if ffmpeg_proc.returncode == 0:
                    print("\nFFmpeg finished successfully.")
                else:
                    Utils.Console.cprint(f'FFmpeg failed with code {ffmpeg_proc.returncode}.', 'red')
                    Utils.Console.cprint(''.join(stderr), 'darkgray')
                    Utils.IO.delete_files(self._output_files)

                return ffmpeg_proc.returncode

            except KeyboardInterrupt:
                Utils.IO.delete_files(self._output_files)
                return Utils.Proc.handle_interrupt(ffmpeg_proc, sox_proc, process_name="FFmpeg")

        # ------- COMMAND OUTPUT PARSERS -------
        def _parse_gstreamer_output_line(
                self, line: str, start_time: float, total_duration: Optional[float] = None) -> Optional[float]:
            """Parse GStreamer progress line and print progress bar."""
            seconds_passed: Optional[float] = None
            if not any(x in line for x in ["Setting pipeline", "PREROLLING", "PREROLLED", "PLAYING", "New clock"]):
                if "progressreport" in line:
                    match = re.search(r"(\d+)\s*/\s*(\d+)\s*seconds\s*\(\s*([\d,\.]+)\s*%\)", line)
                    if match:
                        seconds_passed = float(match.group(1))
                        if total_duration is None:
                            total_duration = float(match.group(2))
                        percent_done = float(match.group(3).replace(",", "."))
                        Utils.update_progress_bar(start_time=start_time, percent_done=percent_done,
                                                  seconds_passed=seconds_passed, total_duration=total_duration)
            return seconds_passed

        def _parse_ffmpeg_output_line(
                self, line: str, start_time: float, total_duration: Optional[float] = None) -> Optional[float]:
            """Parse FFmpeg progress line and print progress bar."""
            line = line.strip()
            seconds_passed: Optional[float] = None
            time_match = re.search(r"time=(\d+):(\d+):(\d+\.\d+)", line)
            if total_duration and time_match:
                h, m, s = time_match.groups()
                seconds_passed = int(h) * 3600 + int(m) * 60 + float(s)
                percent_done = (seconds_passed / total_duration) * 100
                Utils.update_progress_bar(start_time=start_time, percent_done=percent_done,
                                          seconds_passed=seconds_passed, total_duration=total_duration)
            return seconds_passed

        # -------------- COMMAND BUILDERS ---------------
        def _build_gstreamer_thd_command(self) -> List[str]:
            """Build GStreamer command for TrueHD input."""
            return [
                self.parent.gst_launch.as_posix(),
                "--gst-plugin-path", (self.parent.gst_launch.parent / "gst-plugins").as_posix(),
                "--gst-debug=progressreport:5",
                "filesrc", f'location="{self.input_file.as_posix()}"',
                "!", "dlbtruehdparse", "align-major-sync=false",
                "!", "dlbaudiodecbin", "truehddec-presentation=16", f"out-ch-config={self.parent.channels_config_id}",
                "!", f"audio/x-raw,format=F32LE,rate={self.input_frequency},channels={self.parent.channels_count}",
                "!", "progressreport", "update-freq=1", "silent=false",
                "!", "filesink", f'location="{self.temp_raw_file.as_posix()}"'
            ]

        def _build_gstreamer_ac3_command(self) -> List[str]:
            """Build GStreamer command for E-AC3 input."""
            return [
                self.parent.gst_launch.as_posix(),
                "--gst-plugin-path", (self.parent.gst_launch.parent / "gst-plugins").as_posix(),
                "--gst-debug=progressreport:5",
                "filesrc", f'location="{self.input_file.as_posix()}"',
                '!', 'dlbac3parse',
                '!', 'dlbaudiodecbin', 'ac3dec-drc-suppress=true', 'ac3dec-drop-delay=true',
                f'out-ch-config={self.parent.channels_config_id}',
                "!", f"audio/x-raw,format=F32LE,rate={self.input_frequency},channels={self.parent.channels_count}",
                "!", "progressreport", "update-freq=1", "silent=false",
                "!", "filesink", f'location="{self.temp_raw_file.as_posix()}"'
            ]

        def _build_sox_command(self, bits: int, delay: int, volume: float) -> List[str]:
            """Build SoX command for PCM processing."""

            cmd = [
                str(self.parent.sox_launch), "-V1",
                "-t", "f32", "-r", str(self.input_frequency), "-c", str(self.parent.channels_count),
                "--ignore-length", str(self.temp_raw_file)
            ]
            if bits == 32:
                cmd.extend(["-t", "f32", "-e", "floating-point"])
            else:
                cmd.extend(["-t", "s24", "-e", "signed-integer"])
            cmd.extend(["-D", "-"])

            if delay > 0:
                cmd.extend(["pad", f"{delay}s"])
            elif delay < 0:
                cmd.extend(["trim", f"{abs(delay)}s"])
            if volume:
                cmd.extend(["gain", str(int(volume))])
            return cmd

        def _build_ffmpeg_command(self, bits: int, no_numbers: bool, duration: Optional[float],
                                  channels_filter: List[str]) -> List[str]:
            """Build FFmpeg command for channel encoding."""
            ffmpeg_cmd = [
                str(self.parent.ffmpeg_launch),
                "-hide_banner", "-loglevel", "error", "-stats",
                "-f", "s24le" if bits == 24 else "f32le",
                "-ar", str(self.input_frequency),
                "-ac", str(self.parent.channels_count)
            ]
            if duration:
                ffmpeg_cmd.extend(["-t", str(duration)])

            ffmpeg_cmd.extend(["-i", "-"])

            selected_channels = [
                (cid, cname)
                for cid, cname in enumerate(self.parent.channels_layout)
                if not channels_filter or cname in channels_filter
            ]
            if not selected_channels:
                print("\nWarning: no channels selected for output.")
                return []

            self._output_files = [
                self.output_file.with_suffix(
                    f".{cname}.wav" if no_numbers else f".{str(cid + 1).zfill(2)}_{cname}.wav"
                )
                for cid, cname in selected_channels]

            ffmpeg_cmd.extend([
                "-filter_complex",
                f'{";".join(f"[0:a]channelmap={cid}[{cname}]" for cid, cname in selected_channels)}',
                *[
                    item
                    for cid, cname in selected_channels
                    for item in [
                        "-map", f"[{cname}]",
                        "-c:a", "pcm_s24le" if bits == 24 else "pcm_f32le",
                        "-y", str(self.output_file.with_suffix(
                            f".{cname}.wav" if no_numbers else f".{str(cid + 1).zfill(2)}_{cname}.wav"))
                    ]
                ]
            ])
            return ffmpeg_cmd

        def prepare_audio_info(self, force: bool = False) -> None:
            """
            Prepare and update audio file information.
            Uses cached data (_get_raw_info_data), and if force=True or no cached data exists,
            parses the file directly via AudioInfo.parse().
            """

            def _normalize_audio_field(k: str, v: Union[str, int, float, None]) -> Union[str, int, float, None]:
                """Normalize a single audio info field."""
                return (
                    Utils.Format.to_str(v, True) if k in {"format", "channels", "parser_used"}
                    else Utils.Format.to_float(v) if k == "duration"
                    else Utils.Format.to_frequency(v) if k == "freq"
                    else Utils.Format.to_int(v) if v is not None else None
                )

            ai: Optional[Dict[str, Union[str, int, float, None]]] = None

            # Attempt to get cached or precomputed data first
            if not force:
                ai = self._get_raw_audio_info(force=True)
            # If no data or forced update, parse the audio file directly
            if not ai:
                ai = AudioInfo(
                    mediainfo_launch=self.parent.mediainfo_launch,
                    eac3to_launch=self.parent.eac3to_launch
                ).parse(self.input_file).audio_info

            if self.volume is None:
                if ai.get("dialnorm") is None:
                    if ai.get("format") == self.parent.THD and ai.get("parser_used") is None:
                        Utils.Console.cprint("Warning. The 'dialnorm' level for TrueHD audio can only be determined "
                                     "using 'eac3to'.\nThe sound volume will not be adjusted.", "blue")
                    else:
                        Utils.Console.cprint("Warning. Failed to set the audio 'dialnorm' level.\n"
                                     "The sound volume will not be adjusted.", "blue")

            # Update the audio_info dictionary with normalized values
            self.audio_info.update({k: _normalize_audio_field(k, v) for k, v in ai.items()})
            # Update the raw_info_file with normalized values
            self._put_raw_audio_info(self.audio_info)

            Utils.Console.cprint("{format}{channels} info{parser_used}".format(
                format="TrueHD (Atmos)" if self.get_audio_info("format") and self.get_audio_info(
                    "format") == AudioFormat.TRUEHD else "E-AC3",
                channels=f" {self.get_audio_info('channels')}" if self.get_audio_info('channels') else "",
                parser_used=f" got by {self.get_audio_info('parser_used').upper()}" if self.get_audio_info('parser_used') else ""
            ))
            Utils.Console.cprint('duration={duration}{dialnorm}{freq}'.format(
                duration=Utils.Format.to_human_time(self.get_audio_info("duration")),
                dialnorm=f" dialnorm={self.get_audio_info('dialnorm')}" if self.get_audio_info("dialnorm") else "",
                freq=f" freq={ self.get_audio_info('freq')/1000 } kHz" if self.get_audio_info('freq') else ""
            ), 'green')

        def get_audio_info(self, key: Optional[str] = None
                           ) -> Union[Dict[str, Union[str, int, float, None]], str, int, float, None]:
            """
            Return a specific audio parameter if key is provided,
            or the full audio_info dictionary otherwise.
            """
            return self.audio_info if not (key := Utils.Format.to_str(key, True)) else self.audio_info.get(key, None)

        def _get_raw_audio_info(
                self,
                force: bool = False,
                names: Optional[List[str]] = None
        ) -> Optional[Dict[str, Union[str, int, float, None]]]:
            """
            Retrieve cached audio information from temp file.
            If 'force' is True or no cached data exists, reads from self.temp_raw_info_file.
            Optionally filter returned fields by 'names'.
            """
            result: Optional[Dict[str, Union[str, int, float, None]]] = None

            # Get cached data or force read from file
            audio_info: Optional[Dict[str, Union[str, int, float, None]]] = (
                Utils.IO.get_params_from_file(file=self.temp_raw_info_file)
                if force or self.audio_info is None
                else self.audio_info
            )

            if audio_info:
                if names:
                    # Normalize names and discard invalid keys
                    normalized_names = [n for n in (Utils.Format.to_str(name, strip=True) for name in names) if n]
                    if normalized_names:
                        result = {name: audio_info.get(name, None) for name in normalized_names}
                else:
                    result = audio_info
            return result

        def _put_raw_audio_info(self, params: Optional[Dict[str, Any]] = None, **kwargs) -> None:
            """Save raw audio info to temporary file."""
            Utils.IO.put_params_to_file(
                file=self.temp_raw_info_file,
                params=params or {k: v for k, v in kwargs.items() if k not in ("self", "params")}
            )
