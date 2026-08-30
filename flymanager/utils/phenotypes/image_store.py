import io
import uuid

import gridfs


class ImageNotFound(LookupError):
    """Raised when a storage id has no bytes behind it."""


class ImageStore:
    def put(self, data, *, content_type):
        raise NotImplementedError

    def open(self, storage_id):
        raise NotImplementedError

    def delete(self, storage_id):
        raise NotImplementedError

    def exists(self, storage_id):
        raise NotImplementedError


class MemoryImageStore(ImageStore):
    def __init__(self):
        self._objects = {}

    def put(self, data, *, content_type):
        storage_id = uuid.uuid4().hex
        self._objects[storage_id] = (bytes(data), content_type)
        return storage_id

    def open(self, storage_id):
        if storage_id not in self._objects:
            raise ImageNotFound(storage_id)
        return io.BytesIO(self._objects[storage_id][0])

    def delete(self, storage_id):
        self._objects.pop(storage_id, None)

    def exists(self, storage_id):
        return storage_id in self._objects


class GridFSImageStore(ImageStore):
    def __init__(self, db, bucket_name="marker_images"):
        self._bucket = gridfs.GridFSBucket(db, bucket_name=bucket_name)

    def put(self, data, *, content_type):
        object_id = self._bucket.upload_from_stream(
            "marker-image", io.BytesIO(bytes(data)),
            metadata={"contentType": content_type},
        )
        return str(object_id)

    def open(self, storage_id):
        from bson import ObjectId
        from bson.errors import InvalidId
        try:
            return self._bucket.open_download_stream(ObjectId(storage_id))
        except (gridfs.NoFile, InvalidId) as exc:
            raise ImageNotFound(storage_id) from exc

    def delete(self, storage_id):
        from bson import ObjectId
        from bson.errors import InvalidId
        try:
            self._bucket.delete(ObjectId(storage_id))
        except (gridfs.NoFile, InvalidId):
            pass

    def exists(self, storage_id):
        try:
            self.open(storage_id).close()
        except ImageNotFound:
            return False
        return True
