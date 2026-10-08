import os
import sys


def run():
    # updating and uninstalling only need a few small modules: the window and its libraries are never loaded for them
    argv = sys.argv[1:]
    if "--apply-update" in argv:
        from bettercomfy import updater
        i = argv.index("--apply-update")
        return updater.apply_update(argv[i + 1], int(argv[i + 2]), "--no-restart" not in argv)
    if "--uninstall" in argv:
        from bettercomfy import installation
        return installation.uninstall()
    from bettercomfy.app import main
    return main()


if __name__ == "__main__":
    code = run()
    if "--apply-update" in sys.argv or "--uninstall" in sys.argv:
        os._exit(code or 0)            # nothing left to save: no slow shutdown
    sys.exit(code)
