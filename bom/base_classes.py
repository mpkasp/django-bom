class AsDictModel:
    def as_dict(self):
        return dict(self)

    def __iter__(self):
        for key in dir(self):
            if not key.startswith("_"):
                value = getattr(self, key)
                if not callable(value):
                    if isinstance(value, (int, float, complex, bool)):
                        yield key, value
                    else:
                        yield key, str(value)
