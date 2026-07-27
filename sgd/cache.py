import json
import logging
import pickle

logger = logging.getLogger(__name__)


class Cache:
    """A tiny file-backed cache.

    Note: on serverless platforms (e.g. Vercel) '/tmp' is not guaranteed to
    persist or be shared across invocations, so this only reliably helps
    within a single warm instance/request lifetime, not across cold starts.
    """

    def __init__(self, filename, filetype):
        self.filename = '/tmp/'+filename
        self.filetype = filetype
        self.bin = "b" if filetype is pickle else ""
        try:
            self.load()
        except FileNotFoundError:
            self.contents = dict()
            self.save("Created")

    def load(self):
        with open(self.filename, f"r{self.bin}") as file_:
            self.contents = self.filetype.load(file_)
            logger.debug("Reading %s", self.filename)

    def save(self, mess="Saving"):
        with open(self.filename, f"w{self.bin}") as file_:
            self.filetype.dump(self.contents, file_)
            logger.debug("%s %s", mess, self.filename)


class Pickle(Cache):
    def __init__(self, filename):
        super().__init__(filename, pickle)


class Json(Cache):
    def __init__(self, filename):
        super().__init__(filename, json)
