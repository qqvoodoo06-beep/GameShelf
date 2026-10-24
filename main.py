import sys

from gameshelf import i18n

i18n.init()  # язык — до импорта остального: часть текстов задаётся при загрузке модулей

from gameshelf.app import run  # noqa: E402

if __name__ == "__main__":
    sys.exit(run())
