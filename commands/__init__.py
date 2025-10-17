import importlib, os

for file in os.listdir(os.path.dirname(__file__)):
    if file.endswith(".py") and file != "__init__.py":
        module = importlib.import_module(f"{__name__}.{file[:-3]}")
