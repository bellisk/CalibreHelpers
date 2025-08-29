from subprocess import PIPE, STDOUT, check_output
from time import localtime, strftime


class Bcolors:
    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"


def log(msg, color=None, output=True):
    if color:
        line = (
            f"{Bcolors.BOLD}{strftime('%m/%d/%Y %H:%M:%S', localtime())}"
            f"{Bcolors.ENDC}: \t {color}{msg}{Bcolors.ENDC}"
        )
    else:
        line = (
            f"{Bcolors.BOLD}{strftime('%m/%d/%Y %H:%M:%S', localtime())}"
            f"{Bcolors.ENDC}: \t {msg}"
        )
    if output:
        print(line)
        return ""
    else:
        return line + "\n"


def check_subprocess_output(command):
    return check_output(command, shell=True, stderr=STDOUT, stdin=PIPE, text=True)
