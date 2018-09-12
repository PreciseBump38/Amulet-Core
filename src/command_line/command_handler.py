import functools
import glob
import importlib
import os
import re
import shlex
import sys
import time
import traceback
from collections import namedtuple
from typing import Type

from api.data_structures import SimpleStack
from api.paths import COMMANDS_DIR
from command_line import Mode, SimpleCommand, ComplexCommand
from command_line.builtin_commands import PopModeCommand

Command_Entry = namedtuple("Command", ("run", "short_help", "help"))


class ModeStack(SimpleStack):

    def __init__(self, *args, **kwargs):
        super(ModeStack, self).__init__(*args, **kwargs)

    def iter(self):
        for mode in self._data:
            yield mode.display()

    def has_mode(self, mode_class: Type[Mode]):
        return self.get_mode(mode_class) is not None

    def get_mode(self, mode_class: Type[Mode]):
        if not isinstance(mode_class, type):
            raise TypeError("You must pass a Type")

        for mode in self._data:
            if isinstance(mode, mode_class):
                return mode

        return None


class CommandHandler:

    _reserved_commands = ("help", "exit")
    _command_regex = re.compile(r"^[a-zA-Z_]+\d*$")

    def in_mode(self, mode_class: Type[Mode]) -> bool:
        pass

    def get_mode(self, mode_class: Type[Mode]) -> Mode:
        pass

    def __init__(self, wrapper):
        self._io = wrapper

        self.shared_data = {}

        self._modules = []

        self._commands = {}
        self._complex_commands = {}
        self._modes = ModeStack()

        self.in_mode = self._modes.has_mode
        self.get_mode = self._modes.get_mode

        self._load_external = None

        self._load_commands()

    def _load_commands(self, reload=False):
        _io = self._io

        if reload:
            self._commands = {}
            self._complex_commands = {}

        search_path = os.path.join(os.path.dirname(COMMANDS_DIR), "commands")
        sys.path.insert(0, os.path.join(search_path))

        commands = glob.glob(os.path.join(search_path, "*.py"))
        if commands:
            if self._load_external is None:
                comp = _io.completer
                _io.completer = None

                _io.print()

                answer = _io.get_input("Would you like to enable them anyway? (y/n)> ")
                _io.completer = comp
                self._load_external = answer == "y"
            else:
                answer = "y" if self._load_external else "n"

            if answer.lower() == "y":
                failed_modules = []
                with _io.progress_bar(title="Loading 3rd-party_modules") as pb:
                    for cmd in pb(commands):
                        time.sleep(0.25)
                        try:
                            module = importlib.import_module(os.path.basename(cmd)[:-3])
                            self._modules.append(module)
                        except Exception as e:
                            failed_modules.append(os.path.basename(cmd))

                for fail in failed_modules:
                    _io.print()

        simple_commands = SimpleCommand.get_subclasses()
        for cmd in simple_commands:

            if not getattr(cmd, "registered", False):
                continue

            command_func, command_name = cmd.command

            if not self._command_regex.match(command_name) and command_name != "$":
                _io.print(
                    f"Could not enable command {command_name} since it doesn't have a valid command name/prefix", color="red"
                )
                continue

            if command_name in self._reserved_commands:
                _io.print(
                    f"Could not enable command {command_name} since another command uses the same prefix!", color="red"
                )

            command_instance = cmd(self)
            self._commands[command_name] = Command_Entry(
                functools.partial(command_func, command_instance),
                command_instance.short_help,
                command_instance.help,
            )

            _io.completer.add_command(
                command_name, getattr(command_instance, "completer", None)
            )

        complex_commands = ComplexCommand.get_subclasses()
        for cmd in complex_commands:

            if not getattr(cmd, "registered", False):
                continue

            base_command = cmd.base_command
            command_instance = cmd(self)

            self._complex_commands[base_command] = command_instance

            for command_name, func in cmd.sub_commands.items():
                self._commands[f"{base_command}.{command_name}"] = Command_Entry(
                    functools.partial(func, command_instance),
                    command_instance.short_help,
                    functools.partial(command_instance.help, command_name),
                )

                _io.completer.add_command(
                    f"{base_command}.{command_name}",
                    getattr(command_instance, "completer", None),
                )

    def run(self):
        _io = self._io
        while True:
            user_input = _io.get_input(f"{' |'.join(self._modes.iter())}> ")

            if not user_input:
                continue

            if user_input.count('"') % 2 != 0 or user_input.count("'") % 2 != 0:
                _io.print(
                    "=== Error: You do not have an even amount of quotations in your entered command, please re-enter your command", color="red"
                )
                continue

            command_parts = shlex.split(user_input)

            if command_parts[0] == "exit":
                if not self._exit("-f" in command_parts):
                    continue

                break

            if command_parts[0] == PopModeCommand.command:
                self._commands[command_parts[0]].run(command_parts)

            if command_parts[0].startswith("$"):
                self._commands["$"].run(command_parts)
                continue

            if command_parts[0] == "help":
                if len(command_parts) > 1:
                    if command_parts[1] in self._commands:
                        _io.print(
                            f"==== {command_parts[1].capitalize()} Command Help ====", color="green"
                        )
                        self._commands[command_parts[1]].help()
                    elif command_parts[1] in self._complex_commands:
                        _io.print(
                            f"==== {command_parts[1].capitalize()} Command Help ====", color="green"
                        )
                        self._complex_commands[command_parts[1]].help()
                    else:
                        _io.print(
                            f'help: Command "{command_parts[1]}" not recognized', color="red"
                        )
                        continue

                else:
                    _io.print(
                        "============= Registered Commands =============", color="green"
                    )
                    print("help - Displays all registered commands and their summaries")
                    print("exit - Exits the command line interface")
                    for cmd, inst in self._commands.items():
                        if "." in cmd:
                            continue

                        print(f"{cmd} - {inst.short_help():.51}")

                    for ccmd, inst in self._complex_commands.items():
                        print(f"{ccmd} - {inst.short_help():.51}")

                    continue

            if command_parts[0] in self._complex_commands:
                if "-h" in command_parts:
                    _io.print(
                        f"==== {command_parts[0].capitalize()} Command Help ====", color="green"
                    )
                    self._complex_commands[command_parts[0]].help()
                    continue

                else:
                    if "." not in command_parts[0]:
                        if command_parts[0] in self._complex_commands:
                            _io.print(
                                f'"{command_parts[0]}" is not a valid command, try "{command_parts[0]} -h"', color="red"
                            )
                            continue

                        _io.print(
                            f'Command "{command_parts[0]}" is not recognized', color="red"
                        )
                        continue

                    new_command_parts = [f"{command_parts[0]}.{command_parts[1]}"]
                    new_command_parts.extend(command_parts[2:])
                    command_parts = new_command_parts

            if command_parts[0] in self._commands:
                if "-h" in command_parts:
                    _io.print(
                        f"==== {command_parts[0].capitalize()} Command Help ====", color="green"
                    )
                    self._commands[command_parts[0]].help()
                else:
                    if self._modes.is_empty():
                        self._execute_command(command_parts)
                    else:
                        result = self._modes.peek().before_execution(command_parts)
                        if result is None or result:
                            self._execute_command(command_parts)
            else:
                _io.print(f'Command "{command_parts[0]}" is not recognized', color="red")

        return 0

    def enter_mode(self, mode: Mode):
        """
        Enters the supplied Mode, but doesn't add it to the ModeStack unless enter() returns True

        :param mode: An instance of Mode to enter
        """
        if mode.enter():
            self._modes.append(mode)
        else:
            self._io.print(
                f"=== Error: Could not enter mode: {mode.__class__.__name__}", color="yellow"
            )

    def exit_mode(self, force: bool = False) -> bool:
        """
        Exits the most current Mode if the Mode's exit() returns True. If False is returned, the user is
        notified. If ``force`` is True, then the return value of exit() is ignored.

        :param force: True if the return value of exit() is to be ignored
        :return: True if the Mode was successfully exited, False otherwise
        """
        if self._modes.is_empty():
            return False

        mode = self._modes.peek()
        result = mode.exit()
        if force:
            self._modes.pop()
        elif not result:
            self._io.print(
                f"======= Could not exit {mode.display()} ======", color="yellow"
            )
            return False

        else:
            self._modes.pop()
        return True

    def _exit(self, force=False) -> bool:
        while not self._modes.is_empty():
            result = self.exit_mode(force)
            if not result:
                self._io.print(
                    f"======= Could not exit {mode.display()} ======", color="red"
                )
                return False

        return True

    def _execute_command(self, command_parts):
        _io = self._io
        try:
            self._commands[command_parts[0]].run(command_parts)
        except Exception as e:
            cmd = " ".join(command_parts)
            _io.print("==== Begin Exception Stacktrace ====", color="red")
            time.sleep(0.01)
            traceback.print_exc()
            time.sleep(0.01)
            _io.print("==== End Exception Stacktrace ====", color="red")
            _io.print(
                f"=== Error: An Exception has occurred while running command: '{cmd}'",
                color="red"
            )
