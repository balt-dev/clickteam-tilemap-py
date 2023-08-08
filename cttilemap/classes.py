import io
import struct
import sys
import warnings
import zlib
from typing import BinaryIO, TYPE_CHECKING

from cttilemap import errors

if TYPE_CHECKING:
	from _typeshed import SupportsRead

from attrs import define, field


@define
class Tile:
	"""A single tile on a tilemap."""
	x: int = None
	y: int = None
	id: int = 0xFFFF

	@classmethod
	def by_xy(cls, x: int, y: int):
		return cls(x, y)

	@classmethod
	def by_id(cls, identifier: int):
		return cls(id=identifier)


@define
class LayerSettings:
	"""A class grouping together many settings of a layer."""
	offset: list[int, int] = field(factory=lambda: [0, 0])
	scroll: list[float, float] = field(factory=lambda: [0., 0.])
	wrap: list[bool, bool] = field(factory=lambda: [False, False])
	visible: bool = True
	opacity: float = 1.0
	tile_dimensions: list[int, int] = field(factory=lambda: [16, 16])


@define
class SubLayer:
	"""A sublayer of a given layer. Stores tile metadata."""
	_data: list[list[bytes]]
	_default_value: bytes
	_width: int
	_height: int

	@property
	def width(self) -> int:
		return self._width

	@property
	def height(self) -> int:
		return self._height

	@property
	def default_value(self) -> bytes:
		"""The default value for each cell. Resizing this will resize the tilemap's data to match."""
		return self._default_value

	def cell_size(self) -> int:
		"""Gets the current cell size."""
		return len(self.default_value)

	@default_value.setter
	def default_value(self, value):
		if len(self._default_value) == len(value):
			return
		for row in self._data:
			for i, data in enumerate(row):
				# Resize the data to fit, padding with null bytes if necessary
				row[i] = data[:len(value)] + b'\x00' * max(0, len(value) - len(self._default_value))
		self._default_value = value

	def _resize_axis(self, axis: list, size: int):
		"""Internal helper for resizing an axis to a specified length."""
		if size < 0:
			raise ValueError("Axis size target cannot be negative")
		del axis[size:]
		axis.extend(self._default_value for _ in range(size - len(axis)))

	def resize(self, w: int, h: int):
		"""Resize the sublayer, while adjusting the data to fit."""
		if self.width == w and self.height == h:
			return
		self._width = w
		self._height = h
		self._resize_axis(self._data, h)
		for row in self._data:
			self._resize_axis(row, w)

	def __getattr__(self, item) -> bytes:
		"""Get a tile by index of (x,y)."""
		if type(item) != tuple or \
				len(item) != 2 or \
				type(item[0]) != int or \
				type(item[1]) != int:
			raise TypeError("Expected tuple[int, int] for layer access")
		return self._data[item[1]][item[0]]

	def __setattr__(self, item, value):
		"""Set a tile by index of (x,y)."""
		if type(item) != tuple or \
				len(item) != 2 or \
				type(item[0]) != int or \
				type(item[1]) != int:
			raise TypeError("Expected tuple[x: int, y: int] for sublayer access")
		if type(value) != bytes:
			raise TypeError("Expected bytes for sublayer mutation")
		if len(value) != self.cell_size():
			raise ValueError("Supplied value did not have the correct size")
		self._data[item[1]][item[0]] = value


@define
class Layer:
	"""A single layer of a tilemap."""
	_data: list[list[Tile]]
	_width: int
	_height: int
	settings: LayerSettings = field(factory=LayerSettings)
	properties: dict[str, int | float | str] = field(factory=dict)
	sublayers: list[SubLayer] = None

	@property
	def width(self) -> int:
		return self._width

	@property
	def height(self) -> int:
		return self._height

	def resize(self, w: int, h: int):
		"""Resize the sublayer, while adjusting the data to fit."""
		if self.width == w and self.height == h:
			return
		self._width = w
		self._height = h
		del self._data[:h]
		for y, row in enumerate(self._data):
			del row[:w]
			if len(row) < w:
				row.extend(Tile(x, y) for x in range(len(row), w))
		if len(self._data) < h:
			self._data.extend(
				[Tile(x, y) for x in range(w)]
				for y in range(len(self._data), h)
			)

	def __getattr__(self, item) -> Tile:
		"""Get a tile by index of (x,y)."""
		if type(item) != tuple or \
				len(item) != 2 or \
				type(item[0]) != int or \
				type(item[1]) != int:
			raise TypeError("Expected tuple[x: int, y: int] for layer access")
		return self._data[item[1]][item[0]]

	def __setattr__(self, item, value):
		"""Set a tile by index of (x,y)."""
		if type(item) != tuple or \
				len(item) != 2 or \
				type(item[0]) != int or \
				type(item[1]) != int:
			raise TypeError("Expected tuple[x: int, y: int] for layer access")
		if isinstance(value, Tile):
			raise TypeError("Expected Tile for layer mutation")
		self._data[item[1]][item[0]] = value


@define
class TileMap:
	"""An object representing a tilemap."""
	version: int

	@staticmethod
	def _read_short_string(fp: BinaryIO) -> bytes:
		"""Read a short string. Max length of 256 bytes."""
		length = int.from_bytes(fp.read(1), sys.byteorder, signed = False)
		return fp.read(length + 1)

	@staticmethod
	def _read_long_string(fp: BinaryIO) -> bytes:
		"""Read a long string. Max length of 2**32 bytes."""
		length = int.from_bytes(fp.read(4), sys.byteorder, signed = False)
		return fp.read(length + 1)

	@staticmethod
	def _read_compressed_data(fp: BinaryIO) -> bytes:
		"""Read length-preceded compressed data."""
		length = int.from_bytes(fp.read(4), sys.byteorder, signed = False)
		compressed_data = fp.read(length + 1)
		return zlib.decompress(compressed_data)

	@classmethod
	def load(cls, fp: BinaryIO, strict: bool = False):
		"""Load a tilemap from a file."""
		if fp.read(8) != b'ACHTUNG!':
			raise errors.DeserializationError(
				"Wrong magic string or wasn't present. Are you sure you chose a tilemap file?"
			)
		version = int.from_bytes(fp.read(2), sys.byteorder, signed = False)
		version ^= 0b100000000
		if version < 10 or version > 15:
			if strict:
				raise errors.DeserializationError(f"Version {version} isn't supported")
			else:
				warnings.warn(f"Version {version} isn't supported")
		block_id = b''
		block_size = 0
		properties = {}
		while True:
			# Read block
			try:
				block_id, block_size = struct.unpack("@4sI", fp.read(8))
			except EOFError:
				break
			"""
			const unsigned TILE = 'ELIT';
			const unsigned MAP_ = ' PAM';
			const unsigned LAYR = 'RYAL';
			const unsigned MAIN = 'NIAM'; // LAYR sub-block: Main (tile data)
			const unsigned DATA = 'ATAD'; // LAYR sub-block: Data ("sub-layer")
			"""
			if block_id == b'MAP ':
				property_count = int.from_bytes(fp.read(2))

	@classmethod
	def loads(cls, buf: bytes):
		"""Load a tilemap from a string."""
		return cls.load(
			io.BytesIO(buf)
		)
