import argparse
import pathlib
import sys
from typing import Optional, List, Dict, Union
from Utils import Utils


# -------------------------------
# InData singleton
# -------------------------------
class InData:
    # ---------------------------
    # Typed attributes
    # ---------------------------
    input_file: pathlib.Path
    output_file: pathlib.Path

    volume: Optional[int] = None
    channels_filter: List[str]
    no_numbers: bool
    bits: int
    delay: int
    keep_raw: bool

    # ---------------------------
    # Constants
    # ---------------------------
    CHANNELS: Dict[str, Dict]
    BINS_REQ: Dict[str, str] = {'gst': '', 'sox': '', 'ffmpeg': '', 'mediainfo': '', 'eac3to': ''}
    # ---------------------------
    # Private attributes
    # ---------------------------
    _instance: Optional["InData"] = None
    _parser: Optional["_Parser"] = None
    # ---------------------------
    # Used by setter/getter
    # ---------------------------
    _channels: Dict

    # ---------------------------
    # Singleton logic
    # ---------------------------
    def __new__(cls, bins_req: Optional[Dict[str, str]] = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)

            # Dynamically add type annotations for binary paths
            for name in cls.BINS_REQ:
                cls._instance.__annotations__[f"{name}_launch"] = pathlib.Path
        return cls._instance

    def __init__(self, bins_req: Optional[Dict[str, str]] = None):

        initialized = getattr(self, "_initialized", False)
        if not initialized:
            self._initialized = True

            self.BINS_REQ = bins_req

            # ---------------------------
            # Private CHANNELS
            # ---------------------------
            self.CHANNELS: Dict[str, Dict] = {
                '2.0': {'id': 0, 'names': ['L', 'R']},
                '3.1': {'id': 3, 'names': ['L', 'R', 'C', 'LFE']},
                '5.1': {'id': 7, 'names': ['L', 'R', 'C', 'LFE', 'Ls', 'Rs']},
                '7.1': {'id': 11, 'names': ['L', 'R', 'C', 'LFE', 'Ls', 'Rs', 'Lrs', 'Rrs']},
                '9.1': {'id': 12, 'names': ['L', 'R', 'C', 'LFE', 'Ls', 'Rs', 'Lrs', 'Rrs', 'Lw', 'Rw']},
                '9.1.6': {'id': 20, 'names': ['L', 'R', 'C', 'LFE', 'Ls', 'Rs', 'Lrs', 'Rrs',
                                              'Lw', 'Rw', 'Ltf', 'Rtf', 'Ltm', 'Rtm', 'Ltr', 'Rtr']},
            }

            self._Parser(self).parse()

    @property
    def channels(self) -> Dict:
        return self._channels

    @channels.setter
    def channels(self, key: str) -> None:
        self._channels = self.CHANNELS.get(key, '9.1.6')

    # ---------------------------
    # Private parser
    # ---------------------------
    class _Parser:
        config: "InData"
        parser: "argparse.ArgumentParser"

        def __init__(self, config: "InData"):
            self.config = config
            self.parser = argparse.ArgumentParser()
            self._add_arguments()

        class _Converters:
            # ----------------------------
            # Optional path (empty -> None)
            # ----------------------------
            @staticmethod
            def parse_optional_path(value: str) -> Optional[pathlib.Path]:
                return pathlib.Path(value).absolute() if value.strip() else None

            # ----------------------------
            # Required path (must exist)
            # ----------------------------
            @staticmethod
            def parse_required_path(value: str) -> pathlib.Path:
                if not value.strip():
                    raise argparse.ArgumentTypeError("Input path cannot be empty")
                return pathlib.Path(value).absolute()

            # ----------------------------
            # Required path (must exist)
            # ----------------------------
            @staticmethod
            def parse_existing_path(value: str) -> pathlib.Path:
                path = InData._Parser._Converters.parse_required_path(value)
                if not path.exists():
                    raise argparse.ArgumentTypeError(f"Input file must exist, got '{value}'")
                return path

            @staticmethod
            def parse_delay(value: str) -> int:

                result: int
                try:
                    if value.endswith('s'):
                        result = round(float(value[:-1]) * 48000)  # 48000  Hz
                    else:
                        result = int(value)
                except ValueError:
                    raise argparse.ArgumentTypeError(f"Invalid delay format: {value}")
                return result

            @staticmethod
            def parse_volume(value: str) -> Optional[int]:
                result: Optional[int]
                val = value.strip().lower()
                if val == 'auto':
                    result = None
                else:
                    try:
                        result = int(value)
                    except ValueError:
                        raise argparse.ArgumentTypeError(f"Invalid volume value: {value}")
                return result

            @staticmethod
            def parse_bool(value: str) -> bool:
                result: bool
                normalized = value.strip().lower()
                if normalized in ('yes', 'true', '1'):
                    result = True
                elif normalized in ('no', 'false', '0'):
                    result = False
                else:
                    raise argparse.ArgumentTypeError(f"Boolean value expected, got '{value}'")
                return result

            @staticmethod
            def parse_channels_filter(value: str) -> List[str]:
                return stripped.split(",") if (stripped := value.strip(" ,")) else []

        def _add_arguments(self):
            conv = self._Converters

            # Input/output
            self.parser.add_argument('-i', '--input', required=True, metavar='FILENAME',
                                     type=conv.parse_existing_path,
                                     help='Path to source file')
            self.parser.add_argument('-o', '--output', metavar='FILENAME',
                                     type=conv.parse_optional_path,
                                     help='Path to output base file')
            # Binary paths
            for name, path in self.config.BINS_REQ.items():
                self.parser.add_argument(f'-{name}_launch', f'--{name}_launch',
                                         type=pathlib.Path,
                                         default=Utils.IO.absolute_self(path),
                                         help=f'Path to {name}-launch file')

            # Audio options
            self.parser.add_argument('-c', '--channels',
                                     type=str, default='9.1.6', choices=InData().CHANNELS.keys(),
                                     help='Output channel configuration')
            self.parser.add_argument('-cf', '--channels_filter',
                                     type=conv.parse_channels_filter, default=[],
                                     help='Output channel filter (comma separated)')
            self.parser.add_argument(
                '-no_numbers', '--no_numbers',
                # action='store_true'
                nargs='?',  # value is optional
                const=True,  # if used without value -> True
                default=False,  # if not used at all -> False
                type=conv.parse_bool,  # if value is provided -> parse it
                help='Do not use numbers in output channel names (bool)',
            )
            self.parser.add_argument('-v', '--volume', type=conv.parse_volume, default=None,
                                     help="Change volume level (db) or 'auto'")
            self.parser.add_argument('-b', '--bits', type=int, choices=[16, 24, 32], default=24,
                                     help='Encoded sample size in bits')
            self.parser.add_argument('-keep_raw', '--keep_raw',
                                     nargs='?', const=True, default=False, type=conv.parse_bool,
                                     help='Keep raw/intermediate file')
            self.parser.add_argument('-d', '--delay', type=conv.parse_delay, default=0,
                                     metavar='POSITION(s)',
                                     help=(
                                         "Add silence at start (positive) or trim start (negative).\n\n"
                                         "POSITION can be:\n"
                                         "  - Float or integer followed by 's' to indicate seconds (e.g., 1.5s)\n"
                                         "  - Integer without 's' to indicate samples (e.g., 3000)\n\n"
                                         "Default: 0 (no delay).\n\n"
                                         "Examples:\n"
                                         "  --delay 1.5s   => add 1.5 sec of silence\n"
                                         "  --delay -3s    => trim 3 sec\n"
                                         "  --delay 3000   => add 3000 samples of silence\n"
                                         "  --delay -1500  => trim 1500 samples"
                                     ))

        def parse(self):
            args = self.parser.parse_args()
            # Binary paths (loop instead of hardcoded)
            for name in self.config.BINS_REQ:
                setattr(self.config, f"{name}_launch", getattr(args, f"{name}_launch"))
            # Audio options
            self.config.channels = args.channels
            self.config.channels_filter = args.channels_filter
            self.config.no_numbers = args.no_numbers
            self.config.volume = args.volume
            self.config.bits = args.bits
            self.config.keep_raw = args.keep_raw
            self.config.delay = args.delay
            # Input/output
            self.config.input_file = args.input
            self.config.output_file = args.output.with_suffix(".wav") if args.output is not None \
                else args.input.with_suffix(".wav")

