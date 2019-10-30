from __future__ import annotations

from typing import List, Tuple

import numpy
import amulet_nbt as nbt

from amulet.api.block import Block
from amulet.api.chunk import Chunk
from amulet.world_interface.chunk.interfaces import Interface
from amulet.utils.world_utils import get_smallest_dtype, decode_long_array, encode_long_array


def properties_to_string(props: dict) -> str:
    """
    Converts a dictionary of blockstate properties to a string

    :param props: The dictionary of blockstate properties
    :return: The string version of the supplied blockstate properties
    """
    result = []
    for key, value in props.items():
        result.append("{}={}".format(key, value))
    return ",".join(result)


class Anvil2Interface(Interface):
    @staticmethod
    def is_valid(key):
        if key[0] != "anvil":
            return False
        if key[1] < 1444:
            return False
        return True

    def decode(self, data: nbt.NBTFile) -> Tuple[Chunk, numpy.ndarray]:
        """
        Create an amulet.api.chunk.Chunk object from raw data given by the format.
        :param data: nbt.NBTFile
        :return: Chunk object in version-specific format, along with the palette for that chunk.
        """
        misc = {}
        cx = data["Level"]["xPos"].value
        cz = data["Level"]["zPos"].value
        blocks, palette = self._decode_blocks(data["Level"]["Sections"])
        misc['BlockLight2048BA'] = {section['Y'].value: section['BlockLight'] for section in data["Level"]["Sections"]}
        misc['SkyLight2048BA'] = {section['Y'].value: section['SkyLight'] for section in data["Level"]["Sections"]}

        misc['TileTicksA'] = data['Level']['TileTicks']
        misc['LastUpdateL'] = data['Level']['LastUpdate']
        biomes = data['Level']['Biomes'].value
        misc['InhabitedTimeL'] = data['Level']['InhabitedTime']

        misc['StatusSt'] = data['Level']['Status']
        misc['HeightmapsC'] = data['Level']['Heightmaps']
        misc['ToBeTickedA'] = data['Level']['ToBeTicked']
        misc['PostProcessingA'] = data['Level']['PostProcessing']
        misc['StructuresC'] = data['Level']['Structures']
        misc['LiquidTicksA'] = data['Level']['LiquidTicks']
        misc['LiquidsToBeTicked'] = data['Level']['LiquidsToBeTicked']

        entities = self._decode_entities(data["Level"]["Entities"])
        tile_entities = None
        return Chunk(cx, cz, blocks=blocks, entities=entities, tileentities=tile_entities, biomes=biomes, misc=misc, extra=data), palette

    def encode(self, chunk: Chunk, palette: numpy.ndarray, max_world_version: Tuple[str, int]) -> nbt.NBTFile:
        """
        Encode a version-specific chunk to raw data for the format to store.
        :param chunk: The version-specific chunk to translate and encode.
        :param palette: The palette the ids in the chunk correspond to.
        :return: nbt.NBTFile
        """
        misc = chunk.misc
        data = nbt.NBTFile(nbt.TAG_Compound(), '')
        data['Level'] = nbt.TAG_Compound()
        data['Level']['xPos'] = nbt.TAG_Int(chunk.cx)
        data['Level']['zPos'] = nbt.TAG_Int(chunk.cz)
        data['DataVersion'] = nbt.TAG_Int(max_world_version[1])
        data["Level"]["Sections"] = self._encode_blocks(chunk.blocks, palette)
        for section in data["Level"]["Sections"]:
            y = section['Y'].value
            block_light = chunk.misc.get('BlockLight2048', {})
            sky_light = chunk.misc.get('SkyLight2048', {})
            if y in block_light:
                section['BlockLight'] = block_light[y]
            else:
                section['BlockLight'] = nbt.TAG_Byte_Array(numpy.zeros(2048, dtype=numpy.uint8))
            if y in sky_light:
                section['SkyLight'] = sky_light[y]
            else:
                section['SkyLight'] = nbt.TAG_Byte_Array(numpy.zeros(2048, dtype=numpy.uint8))

        data['Level']['TileTicks'] = misc.get('TileTicksA', nbt.TAG_List())
        data['Level']['LastUpdate'] = misc.get('LastUpdateL', nbt.TAG_Long(0))
        data['Level']['Biomes'] = nbt.TAG_Int_Array(chunk.biomes.convert_to_format(256).astype(dtype=numpy.uint32))
        data['Level']['InhabitedTime'] = misc.get('InhabitedTimeL', nbt.TAG_Long(0))

        data['Level']['Status'] = misc.get('StatusSt', nbt.TAG_String('postprocessed'))
        data['Level']['Heightmaps'] = nbt.TAG_Compound()
        heightmaps = misc.get('HeightmapsC', nbt.TAG_Compound())
        for heightmap in (
                'MOTION_BLOCKING',
                'MOTION_BLOCKING_NO_LEAVES',
                'OCEAN_FLOOR',
                'OCEAN_FLOOR_WG',
                'WORLD_SURFACE',
                'WORLD_SURFACE_WG'
        ):
            data['Level']['Heightmaps'][heightmap] = heightmaps.get(
                heightmap, nbt.TAG_Long_Array(numpy.zeros(36, dtype='>i8'))
            )
        data['Level']['ToBeTicked'] = misc.get('ToBeTickedA', nbt.TAG_List([nbt.TAG_List() for _ in range(16)]))
        data['Level']['PostProcessing'] = misc.get('PostProcessingA', nbt.TAG_List([nbt.TAG_List() for _ in range(16)]))
        data['Level']['Structures'] = misc.get('StructuresC', nbt.TAG_Compound())
        data['Level']['LiquidTicks'] = misc.get('LiquidTicksA', nbt.TAG_List())
        data['Level']['LiquidsToBeTicked'] = misc.get('LiquidsToBeTicked', nbt.TAG_List([nbt.TAG_List() for _ in range(16)]))

        data["Level"]["Entities"] = self._encode_entities(chunk.entities)
        return data

    def _decode_blocks(
        self, chunk_sections
    ) -> Tuple[numpy.ndarray, numpy.ndarray]:
        if not chunk_sections:
            raise NotImplementedError(
                "We don't support reading chunks that never been edited in Minecraft before"
            )

        blocks = numpy.zeros((256, 16, 16), dtype=int)
        palette = [Block(namespace="minecraft", base_name="air")]

        for section in chunk_sections:
            if "Palette" not in section:  # 1.14 makes palette/blocks optional.
                continue
            height = section["Y"].value << 4

            blocks[height: height + 16, :, :] = decode_long_array(
                section["BlockStates"].value, 4096
            ).reshape((16, 16, 16)) + len(palette)

            palette += self._decode_palette(section["Palette"])

        blocks = numpy.swapaxes(blocks.swapaxes(0, 1), 0, 2)
        palette, inverse = numpy.unique(palette, return_inverse=True)
        blocks = inverse[blocks]

        return blocks.astype(f"uint{get_smallest_dtype(blocks)}"), palette

    def _encode_blocks(self, blocks: numpy.ndarray, palette: numpy.ndarray) -> nbt.TAG_List:
        sections = nbt.TAG_List()
        for y in range(16):  # perhaps find a way to do this dynamically
            block_sub_array = blocks[:, y * 16: y * 16 + 16, :].ravel()

            sub_palette_, block_sub_array = numpy.unique(block_sub_array, return_inverse=True)
            sub_palette = self._encode_palette(palette[sub_palette_])
            if len(sub_palette) == 1 and sub_palette[0]['Name'].value == 'minecraft:air':
                continue

            section = nbt.TAG_Compound()
            section['Y'] = nbt.TAG_Byte(y)
            section['BlockStates'] = nbt.TAG_Long_Array(encode_long_array(block_sub_array))
            section['Palette'] = sub_palette
            sections.append(section)

        return sections

    @staticmethod
    def _decode_palette(palette: nbt.TAG_List) -> list:
        blockstates = []
        for entry in palette:
            namespace, base_name = entry["Name"].value.split(":", 1)
            # TODO: handle waterlogged property
            properties = {prop: str(val.value) for prop, val in entry.get("Properties", nbt.TAG_Compound({})).items()}
            block = Block(
                namespace=namespace, base_name=base_name, properties=properties
            )
            blockstates.append(block)
        return blockstates

    @staticmethod
    def _encode_palette(blockstates: list) -> nbt.TAG_List:
        palette = nbt.TAG_List()
        for block in blockstates:
            entry = nbt.TAG_Compound()
            entry['Name'] = nbt.TAG_String(f'{block.namespace}:{block.base_name}')
            properties = entry['Properties'] = nbt.TAG_Compound()
            # TODO: handle waterlogged property
            for prop, val in block.properties.items():
                if isinstance(val, str):
                    properties[prop] = nbt.TAG_String(val)
            palette.append(entry)
        return palette

    def _decode_entities(self, entities: list) -> List[nbt.NBTFile]:
        return []
        # entity_list = []
        # for entity in entities:
        #     entity = nbt_template.create_entry_from_nbt(entity)
        #     entity = self._entity_handlers[entity["id"].value].load_entity(entity)
        #     entity_list.append(entity)
        #
        # return entity_list

    def _encode_entities(self, entities: list) -> nbt.TAG_List:
        return nbt.TAG_List([])

    def _get_translator_info(self, data: nbt.NBTFile) -> Tuple[Tuple[str, int], int]:
        return ("anvil", data["DataVersion"].value), data["DataVersion"].value


INTERFACE_CLASS = Anvil2Interface
