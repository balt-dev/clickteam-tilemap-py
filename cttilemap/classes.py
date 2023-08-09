import io
import struct
import sys
import zlib
from io import BytesIO
from typing import BinaryIO

from cttilemap import errors

from attrs import define, field


@define
class Tile:
	"""A single tile on a tilemap."""
	id: int = 0xFFFF

	# This is a union of (x,y) or (id) in the original C++

	@property
	def x(self):
		return (self.id & 0xFF00) >> 8

	@x.setter
	def x(self, value):
		value %= 0x100
		self.id = (value << 8) + (self.id & 0xFF)

	@property
	def y(self):
		return self.id & 0xFF

	@y.setter
	def y(self, value):
		value %= 0x100
		self.id = (self.id & 0xFF00) + value

	@classmethod
	def by_xy(cls, x: int, y: int):
		x %= 0x100
		y %= 0x100
		return cls((x << 8) + y)

	@classmethod
	def by_id(cls, identifier: int):
		identifier %= 0x10000
		return cls(id=identifier)

	def __repr__(self):
		return f"Tile({self.id:04X})"


@define
class SubLayerLink:
	"""A linkage to a sublayer within a layer."""
	tileset: int = 0xFF
	animation: int = 0xFF
	animation_frame: int = 0xFF


@define
class LayerSettings:
	"""A class grouping together many settings of a layer."""
	tileset: int = 0
	collision: int = 0
	offset: list[int, int] = field(factory=lambda: [0, 0])
	scroll: list[float, float] = field(factory=lambda: [0., 0.])
	wrap: list[bool, bool] = field(factory=lambda: [False, False])
	visible: bool = True
	opacity: float = 1.0
	tile_dimensions: list[int, int] = field(factory=lambda: [16, 16])
	sublayer_link: SubLayerLink = field(factory=SubLayerLink)


@define
class SubLayer:
	"""A sublayer of a given layer. Stores tile metadata."""
	_data: bytes
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
		oldlen = self.cell_size()
		if oldlen == len(value):
			return
		new_data = []
		for index in range(0, len(self._data), oldlen):
			data = self._data[index:index + oldlen]
			new_data.append(
				data[:len(value)] + b'\x00' * max(0, len(value) - oldlen)
			)
		new_data = b''.join(new_data)
		self._data = new_data
		self._default_value = value

	def resize(self, w: int, h: int):
		"""Resize the sublayer, while adjusting the data to fit."""
		if self.width == w and self.height == h:
			return
		rows = []
		step = self.width * self.cell_size()
		if step > 0:
			for index in range(0, len(self._data), step):
				row_slice = self._data[index:index + step]
				row_slice = row_slice[:w * step]
				if len(row_slice) < w * step:
					row_slice = row_slice + self.default_value * (w - (self.width // self.cell_size()))
				rows.append(row_slice)
		rows = rows[:h]
		while len(rows) < h:
			rows.append(
				self.default_value * w
			)
		self._width = w
		self._height = h
		self._data = b''.join(rows)

	def __getitem__(self, item) -> bytes:
		"""Get a tile by index of (x,y)."""
		if type(item) != tuple or \
				len(item) != 2 or \
				type(item[0]) != int or \
				type(item[1]) != int:
			raise TypeError("Expected tuple[int, int] for layer access")
		start = (item[1] * self.width + item[0]) * self.cell_size()
		return self._data[start:start + self.cell_size()]

	def __setitem__(self, item, value):
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
		start = (item[1] * self.width + item[0]) * self.cell_size()
		self._data = self._data[:start] + value + self._data[start + self.cell_size():]

	def __repr__(self):
		if self.width <= 0 or self.height <= 0:
			return f"Layer({self.width} by {self.height})"
		rows = []
		step = self.width * self.cell_size()
		for index in range(0, len(self._data), step):
			row_data = self._data[index:index + step]
			row = []
			for jndex in range(0, len(row_data), self.cell_size()):  # jndex lol
				row.append(row_data[jndex: jndex + self.cell_size()].hex().upper())
			rows.append(" ".join(row))
		rows = "\n\t".join(rows)
		return f"SubLayer(data=\n\t{rows},\ndefault={self.default_value})"


@define
class Layer:
	"""A single layer of a tilemap."""
	_data: list[Tile] = field(factory=list)
	_width: int = 0
	_height: int = 0
	settings: LayerSettings = field(factory=LayerSettings)
	properties: dict[str, int | float | bytes] = field(factory=dict)
	sublayers: list[SubLayer] = field(factory=list)

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
		new_rows = []
		if self.width > 0:
			for index in range(0, self.width * self.height, self.width):
				row_slice = self._data[index: index + self.width]
				row_slice = row_slice[:w]
				if len(row_slice) < w:
					row_slice.extend(Tile.by_id(0xFFFF) for _ in range(len(row_slice), self.width))
				new_rows.append(row_slice)
		else:
			new_rows = []
		new_rows = new_rows[:h]
		if len(new_rows) < h:
			for y in range(len(new_rows), h):
				new_rows.append([Tile.by_id(0xFFFF) for _ in range(w)])
		new_rows = sum(new_rows, [])
		self._width = w
		self._height = h
		self._data = new_rows

	def __getitem__(self, item) -> Tile:
		"""Get a tile by index of (x,y)."""
		if type(item) != tuple or \
				len(item) != 2 or \
				type(item[0]) != int or \
				type(item[1]) != int:
			raise TypeError("Expected tuple[x: int, y: int] for layer access")
		return self._data[item[1] * self.width + item[0]]

	def __setitem__(self, item, value):
		"""Set a tile by index of (x,y)."""
		if type(item) != tuple or \
				len(item) != 2 or \
				type(item[0]) != int or \
				type(item[1]) != int:
			raise TypeError("Expected tuple[x: int, y: int] for layer access")
		if not isinstance(value, Tile):
			raise TypeError("Expected Tile for layer mutation")
		self._data[item[1] * self.width + item[0]] = value

	def __repr__(self):
		rows = []
		if self.width <= 0 or self.height <= 0:
			return f"Layer({self.width} by {self.height})"
		for index in range(0, len(self._data), self.width):
			row = self._data[index:index + self.width]
			row = " ".join(f"{tile.id:04X}" for tile in row)
			rows.append(row)
		rows = "\n\t".join(rows)
		return f"Layer(data=\n\t{rows},\nsettings={self.settings}, properties={self.properties}, sublayers={self.sublayers})"


@define
class Tileset:
	"""A tileset of the given tilemap."""
	path: str
	transparent_color: list[int, int, int]


class Header:
	"""A helper class to simplify writing headers."""

	def __init__(self, buf: BinaryIO, header: bytes):
		self.stream = buf
		self.buffer = BytesIO()
		self.header = header

	def __enter__(self):
		return self.buffer

	def __exit__(self, *_):
		self.stream.write(self.header)
		self.stream.write(self.buffer.tell().to_bytes(4, sys.byteorder, signed=False))
		self.stream.write(self.buffer.getvalue())


@define
class TileMap:
	"""An object representing a tilemap."""
	version: int
	layers: list[Layer]
	tilesets: list[Tileset]
	properties: dict[str, int | float | bytes]  # Leaving the value as bytes because it really could be anything

	tile_dimensions: list[int, int]

	@staticmethod
	def _read_short_string(fp: BinaryIO) -> str:
		"""Read a short string. Max length of 256 bytes."""
		length = int.from_bytes(fp.read(1), sys.byteorder, signed=False)
		return fp.read(length + 1).decode("UTF-8", "ignore")

	@staticmethod
	def _read_long_string(fp: BinaryIO) -> str:
		"""Read a long string. Max length of 2**32 bytes."""
		length = int.from_bytes(fp.read(4), sys.byteorder, signed=False)
		return fp.read(length + 1).decode("UTF-8", "ignore")

	@staticmethod
	def _read_compressed_data(fp: BinaryIO) -> bytes:
		"""Read length-preceded compressed data."""
		length = int.from_bytes(fp.read(4), sys.byteorder, signed=False)
		compressed_data = fp.read(length)
		return zlib.decompress(compressed_data)

	@classmethod
	def load(cls, buf: BinaryIO):
		"""Load a tilemap from a file."""
		if buf.read(8) != b'ACHTUNG!':
			raise errors.DeserializationError(
				"Wrong magic string or wasn't present. Are you sure you chose a tilemap file?"
			)
		version = int.from_bytes(buf.read(2), sys.byteorder, signed=False)
		version ^= 0b100000000
		if version < 0 or version > 5:
			raise errors.DeserializationError(f"Version {version} isn't supported")
		properties = {}
		tilesets = []
		layers = []
		tile_dimensions = [16, 16]
		while True:
			# Read block
			try:
				block_id, block_size = struct.unpack("=4sI", buf.read(8))
			except struct.error:  # EOF
				break
			"""
			const unsigned TILE = 'ELIT';
			const unsigned MAP_ = ' PAM';
			const unsigned LAYR = 'RYAL';
			const unsigned MAIN = 'NIAM'; // LAYR sub-block: Main (tile data)
			const unsigned DATA = 'ATAD'; // LAYR sub-block: Data ("sub-layer")
			"""
			if block_id == b'MAP ':
				if version >= 3:
					property_count = int.from_bytes(buf.read(2), sys.byteorder, signed=False)
					for _ in range(property_count):
						name = TileMap._read_short_string(buf)
						ty = buf.read(1)
						if ty == b'\x00':  # Integer
							properties[name] = int.from_bytes(
								buf.read(4),
								sys.byteorder,
								signed=True
							)
						elif ty == b'\x01':  # Float
							properties[name] = struct.unpack(
								"=f",
								buf.read(4)
							)[0]
						elif ty == b'\x02':  # Long String
							properties[name] = TileMap._read_long_string(buf)
						else:
							raise errors.DeserializationError(
								f"Invalid type {ty} for mapping"
							)
				elif version >= 0:
					# Deprecated, but necessary for old formats
					tile_dimensions = list(
						struct.unpack("=2H", buf.read(4))
					)
				else:
					tile_dimensions = list(
						struct.unpack("=2B", buf.read(2))
					)
			elif block_id == b'TILE':
				tileset_amount = int.from_bytes(
					buf.read(1),
					sys.byteorder,
					signed=False
				)
				for _ in range(tileset_amount):
					color = list(  # xBGR -> RGB
						struct.unpack(
							"=x3B",
							buf.read(4)
						)
					)[::-1]
					path = TileMap._read_short_string(buf)
					tilesets.append(
						Tileset(
							path,
							color
						)
					)
			elif block_id == b'LAYR':
				layer_count = int.from_bytes(
					buf.read(1 if version < 1 else 2),
					sys.byteorder,
					signed=False
				)
				for _ in range(layer_count):
					layer = Layer()
					w, h = struct.unpack(
						"=2I", buf.read(8)
					)
					layer.resize(w, h)
					if version >= 2:
						layer.settings.tile_dimensions = list(struct.unpack(
							"=2H", buf.read(4)
						))
					else:
						layer.settings.tile_dimensions = tile_dimensions.copy()

					(
						layer.settings.tileset,
						layer.settings.collision,
						offsetx, offsety,
						scrollx, scrolly,
						wrapx, wrapy,
						layer.settings.visible,
						layer.settings.opacity
					) = struct.unpack(
						"=2B2i2f3?f",
						buf.read(25)
					)
					layer.settings.offset = [offsetx, offsety]
					layer.settings.scroll = [scrollx, scrolly]
					layer.settings.wrap = [wrapx, wrapy]
					if version == 4:
						(
							layer.settings.sublayer_link.tileset,
							layer.settings.sublayer_link.animation
						) = struct.unpack(
							"=2B", buf.read(2)
						)
					elif version == 5:
						(
							layer.settings.sublayer_link.tileset,
							layer.settings.sublayer_link.animation,
							layer.settings.sublayer_link.animation_frame
						) = struct.unpack(
							"=3B", buf.read(3)
						)

					data_block_count = int.from_bytes(
						buf.read(1),
						sys.byteorder,
						signed=False
					)
					for _ in range(data_block_count):
						header = struct.unpack(
							"=4s",
							buf.read(4)
						)[0]
						if header == b'MAIN':
							raw_data = TileMap._read_compressed_data(buf)
							tiles = []
							for i in range(0, len(raw_data), 2):  # A tile is 2 bytes long
								tiles.append(Tile.by_id(
									int.from_bytes(raw_data[i:i + 2], "big", signed=False)
								))
							layer._data = tiles  # It's better to assign a private attribute here
						elif header == b'DATA':
							cell_size, default_value = struct.unpack(
								"=B4s", buf.read(5)
							)
							default_value = default_value[:cell_size]
							while len(default_value) < cell_size:
								# Pad with null bytes
								default_value = default_value + b'\x00'
							layer.sublayers.append(
								SubLayer(b'', default_value, 0, 0)
							)
							layer.sublayers[-1].resize(layer.width, layer.height)
							raw_data = TileMap._read_compressed_data(buf)
							layer.sublayers[-1]._data = raw_data  # Again, easier to assign to private
						else:
							raise errors.DeserializationError(
								f"Layer subheader {header} is not valid"
							)
					layers.append(layer)
			else:
				raise EOFError
		return cls(
			version,
			layers,
			tilesets,
			properties,
			tile_dimensions
		)

	@classmethod
	def loads(cls, buf: bytes):
		"""Load a tilemap from a string. Slower compared to load."""
		return cls.load(
			io.BytesIO(buf)
		)

	@classmethod
	def _write_short_string(cls, fp: BinaryIO, data: bytes):
		"""Write a short string, preceded by its length, to the buffer."""
		data = data[:0x100]
		fp.write(len(data).to_bytes(1, sys.byteorder, signed=False))
		fp.write(data)

	@classmethod
	def _write_long_string(cls, fp: BinaryIO, data: bytes):
		"""Write a long string, preceded by its length, to the buffer."""
		data = data[:0x100000000]
		fp.write(len(data).to_bytes(4, sys.byteorder, signed=False))
		fp.write(data)

	@classmethod
	def _write_compressed_data(cls, fp: BinaryIO, data: bytes):
		"""Compress and write data, preceded by its length, to the buffer."""
		compressed_data = zlib.compress(data, 9)
		fp.write(len(compressed_data).to_bytes(4, sys.byteorder, signed=False))
		fp.write(compressed_data)

	def dump(self, buf: BinaryIO):
		"""Dump a tilemap to a file."""
		buf.write(b'ACHTUNG!')  # Magic string
		# Always write version 5
		buf.write(((1 << 8) | 5).to_bytes(2, sys.byteorder, signed=False))
		if len(self.properties):
			with Header(buf, b'MAP ') as hbuf:
				if len(self.properties) > 0xFFFF:
					raise errors.SerializationError("Too many properties")
				hbuf.write(len(self.properties).to_bytes(2, sys.byteorder, signed=False))
				for key, value in self.properties:
					# Type hints for my type checker
					key: str
					value: int | float | bytes
	
					TileMap._write_short_string(hbuf, key.encode("UTF-8", "ignore"))
					ty = type(value)
					if ty == int:
						hbuf.write(b'\x00')
						hbuf.write(value.to_bytes(4, sys.byteorder, signed=True))
					elif ty == float:
						hbuf.write(b'\x01')
						hbuf.write(struct.pack(
							"=f", value
						))
					elif ty == bytes:
						hbuf.write(b'\x02')
						TileMap._write_long_string(hbuf, value)
		if len(self.tilesets):
			with Header(buf, b'TILE') as hbuf:
				if len(self.tilesets) > 0xFF:
					raise errors.SerializationError("Too many tilesets")
				hbuf.write(len(self.tilesets).to_bytes(1, sys.byteorder, signed=False))
				for tileset in self.tilesets:
					hbuf.write(struct.pack(
						"=x3B",
						*tileset.transparent_color[::-1]  # RGB -> xBGR
					))
					TileMap._write_short_string(hbuf, tileset.path.encode("UTF-8", "ignore")[:256])
		if len(self.layers):
			with Header(buf, b'LAYR') as hbuf:
				if len(self.layers) > 0xFFFF:
					raise errors.SerializationError("Too many layers")
				hbuf.write(len(self.layers).to_bytes(2, sys.byteorder, signed=False))
				for layer in self.layers:
					hbuf.write(struct.pack(
						"=2I2H2B2i2f3?f3B",
						layer.width, layer.height,
						*layer.settings.tile_dimensions,
						layer.settings.tileset,
						layer.settings.collision,
						*layer.settings.offset,
						*layer.settings.scroll,
						*layer.settings.wrap,
						layer.settings.visible,
						layer.settings.opacity,
						layer.settings.sublayer_link.tileset,
						layer.settings.sublayer_link.animation,
						layer.settings.sublayer_link.animation_frame
					))
					if layer.width <= 0 or layer.height <= 0:
						hbuf.write(b'\x00')
						continue
					hbuf.write(
						(len(layer.sublayers) + 1).to_bytes(1, sys.byteorder, signed=False)
					)
					hbuf.write(b'MAIN')
					raw_layer = b''.join((tile.id.to_bytes(2, "big") for tile in layer._data))
					TileMap._write_compressed_data(hbuf, raw_layer)
					for sublayer in layer.sublayers:
						default = sublayer.default_value
						default = default[:4]
						while len(default) < 4:
							default = default + b'\x00'
						hbuf.write(b'DATA')
						hbuf.write(struct.pack(
							"=B4s",
							len(sublayer.default_value),
							default
						))
						TileMap._write_compressed_data(hbuf, sublayer._data)

	def dumps(self):
		"""Dump a tilemap to a byte string. Slower compared to dump."""
		buf = BytesIO()
		self.dump(buf)
		return buf.getvalue()
