"""Quantum Inspire library

Copyright 2019 QuTech Delft

qilib is available under the [MIT open-source license](https://opensource.org/licenses/MIT):

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit
persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the
Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""

from operator import itemgetter
from typing import Any, List, Optional

import numpy as np
from bson import ObjectId
from bson.codec_options import TypeCodec, CodecOptions, TypeRegistry
from pymongo import MongoClient

from qilib.data_set.mongo_data_set_io import MongoDataSetIO, NumpyKeys
from qilib.utils.storage.interface import (NoDataAtKeyError,
                                           NodeAlreadyExistsError,
                                           StorageInterface)


class NumpyArrayCodec(TypeCodec):  # type: ignore

    @property
    def python_type(self) -> Any:
        return np.ndarray

    def transform_python(self, value: Any) -> Any:
        return MongoDataSetIO.encode_numpy_array(value)

    @property
    def bson_type(self) -> Any:
        return dict

    def transform_bson(self, value: Any) -> Any:
        if NumpyKeys.OBJECT in value and value[NumpyKeys.OBJECT] == np.array.__name__:
            return MongoDataSetIO.decode_numpy_array(value)

        return value


class StorageMongoDb(StorageInterface):
    """ Reference implementation of StorageInterface with an mongodb backend

    Implements a storage tree in a MongoDB collection
    """

    def __init__(self, name: str, host: str = 'localhost', port: int = 27017, database: str = ''):
        """ MongoDB implementation of storage class

        See also: `StorageInterface`

        Args:
            name: Symbolic name for the storage instance.
            host: MongoDB host
            port: MongoDB port
            database: The database to use, if empty the name of the storage is used
        """
        super().__init__(name)

        self._client = MongoClient(host, port)
        self._db = self._client.get_database(database or name)
        type_registry = TypeRegistry([NumpyArrayCodec()])
        codec_options = CodecOptions(type_registry=type_registry)
        self._collection = self._db.get_collection('storage', codec_options=codec_options)

    def _get_root(self) -> ObjectId:
        """ Get or create a root node if it doesn't exist yet

        Returns:
            An ObjectID of the root node
        """

        node = self._collection.find_one({'tag': '', 'parent': {'$exists': False}})

        if node is not None:
            return node['_id']
        else:
            return self._collection.insert_one({'tag': ''}).inserted_id

    def _retrieve_nodes_by_tag(self, tag: List[str], parent: ObjectId) -> List[str]:
        """ Traverse the tree and list the children of a given tag

        Args:
            tag: The node tag
            parent: The ObjectID of the node's parent

        Returns:
            A list of names of the children
        """

        if len(tag) == 0:
            return list(map(itemgetter('tag'), self._collection.find({'parent': parent, 'tag': {'$exists': True}})))

        else:
            doc = self._collection.find_one({'parent': parent, 'tag': tag[0]})
            if doc is None:
                raise NoDataAtKeyError(f'Tag "{tag[0]}" cannot be found')
            elif 'value' in doc:
                raise NoDataAtKeyError(f'Tag "{tag[0]}" is not a node')
            else:
                return self._retrieve_nodes_by_tag(tag[1:], doc['_id'])

    def _retrieve_value_by_tag(self, tag: List[str], parent: ObjectId) -> Any:
        """ Traverse the tree and give the value a given leaf tag

        Args:
            tag: The leaf tag
            parent: The ObjectID of the leaf's parent

        Returns:
            Data held by the leaf
        """

        if len(tag) == 1:
            doc = self._collection.find_one({'parent': parent, 'tag': tag[0]})
            if doc is None:
                raise NoDataAtKeyError(f'Tag "{tag[0]}" cannot be found')
            elif 'value' not in doc:
                raise NoDataAtKeyError(f'Tag "{tag[0]}" is not a leaf')
            else:
                return doc['value']

        else:
            doc = self._collection.find_one({'parent': parent, 'tag': tag[0], 'value': {'$exists': False}})
            if doc is None:
                raise NoDataAtKeyError(f'Tag "{tag[0]}" cannot be found')
            else:
                return self._retrieve_value_by_tag(tag[1:], doc['_id'])

    def _store_value_by_tag(self, tag: List[str], data: Any, parent: ObjectId) -> None:
        """ Store a value at a given tag

        Args:
            tag: The tag
            data: Data to store
            parent: An ObjectID of the node's parent
        """

        if len(tag) == 1:
            doc = self._collection.find_one({'parent': parent, 'tag': tag[0]})
            if doc:
                if 'value' not in doc:
                    raise NodeAlreadyExistsError(f'Tag "{tag[0]}" is not a leaf')
                else:
                    self._collection.update_one({'parent': parent, 'tag': tag[0]}, {'$set': {'value': data}})
            else:
                self._collection.insert_one({'parent': parent, 'tag': tag[0], 'value': data})

        else:
            doc = self._collection.find_one({'parent': parent, 'tag': tag[0]})
            if doc is None:
                parent = self._collection.insert_one({'parent': parent, 'tag': tag[0]}).inserted_id
            else:
                if 'value' in doc:
                    raise NodeAlreadyExistsError(f'Tag "{tag[0]}" is a leaf')
                else:
                    parent = doc['_id']

            self._store_value_by_tag(tag[1:], data, parent)

    def load_data(self, tag: List[str]) -> Any:
        if not isinstance(tag, list):
            raise TypeError('tag should be a list of strings')

        if len(tag) == 0:
            raise NoDataAtKeyError('Tag cannot be empty')

        return self._unserialize(self._retrieve_value_by_tag(tag, self._get_root()))

    def save_data(self, data: Any, tag: List[str]) -> None:
        if not isinstance(tag, list):
            raise TypeError('tag should be a list of strings')

        self._store_value_by_tag(tag, self._serialize(data), self._get_root())

    def get_latest_subtag(self, tag: List[str]) -> Optional[List[str]]:
        child_tags = sorted(self.list_data_subtags(tag))
        if len(child_tags) == 0:
            return None

        return tag + [child_tags[-1]]

    def list_data_subtags(self, tag: List[str]) -> List[str]:
        try:
            tags = self._retrieve_nodes_by_tag(tag, self._get_root())
        except NoDataAtKeyError:
            tags = []

        return tags

    def search(self, query: str) -> Any:
        raise NotImplementedError()

    def tag_in_storage(self, tag: List[str]) -> bool:
        parent = self._get_root()
        for tag_part in tag:
            doc = self._collection.find_one({'parent': parent, 'tag': tag_part})
            if doc is None:
                return False
            else:
                parent = doc['_id']
        return True