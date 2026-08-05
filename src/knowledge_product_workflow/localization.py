"""Chinese user-facing CLI help formatting and shared messages."""

from __future__ import annotations

import sys
from typing import Any

from typer.core import TyperCommand, TyperGroup

try:  # Typer 0.27 vendors Click; older Typer exposes the external dependency.
    from typer.core import _click
except ImportError:  # pragma: no cover - exercised by older host installations.
    import click as _click


def _localized_usage(command: Any, ctx: Any) -> str:
    pieces = " ".join(command.collect_usage_pieces(ctx))
    return pieces.replace("[OPTIONS]", "[选项]").replace("COMMAND [ARGS]...", "命令 [参数]...")


def _format_parameters(command: Any, ctx: Any, formatter: Any) -> None:
    arguments = []
    options = []
    for parameter in command.get_params(ctx):
        record = parameter.get_help_record(ctx)
        if record is None:
            continue
        translated = _translate_help_record(record)
        if parameter.param_type_name == "argument":
            arguments.append(translated)
        elif parameter.param_type_name == "option":
            options.append(translated)
    if arguments:
        with formatter.section("参数"):
            formatter.write_dl(arguments)
    if options:
        with formatter.section("选项"):
            formatter.write_dl(options)


def _translate_help_record(record: tuple[str, str]) -> tuple[str, str]:
    """Translate deterministic Click parameter annotations without changing flags."""
    name, description = record
    translations = {
        "Show this message and exit.": "显示此帮助信息并退出。",
        "[required]": "[必填]",
        "[default: ": "[默认值: ",
        "[x>=1]": "[必须大于等于 1]",
    }
    for source, target in translations.items():
        name = name.replace(source, target)
        description = description.replace(source, target)
    return name, description


def _translate_usage_error(message: str) -> str:
    """Translate stable Click parser messages while preserving the field hint."""
    if message.startswith("Missing argument "):
        return "缺少必填参数：" + message.removeprefix("Missing argument ")
    if message.startswith("Missing option "):
        return "缺少必填选项：" + message.removeprefix("Missing option ")
    if message.startswith("No such option: "):
        return "未知选项：" + message.removeprefix("No such option: ")
    if message.startswith("Invalid value"):
        return "参数值无效：" + message.removeprefix("Invalid value").lstrip(": ")
    return message


class _ChineseUsageError(_click.exceptions.UsageError):
    """A parser failure whose complete reader-facing shell output is Chinese."""

    def show(self, file: Any | None = None) -> None:
        output = file or sys.stderr
        if self.ctx is not None:
            _click.echo(self.ctx.get_usage(), file=output, color=self.ctx.color)
            _click.echo(f"请运行 '{self.ctx.command_path} --help' 查看帮助。", file=output, color=self.ctx.color)
        _click.echo(f"错误：{_translate_usage_error(self.format_message())}", file=output, color=False)


class _ChineseHelpMixin:
    """Render Click/Typer help without English framework headings."""

    def format_help(self, ctx: Any, formatter: Any) -> None:
        formatter.write_text(f"用法: {ctx.command_path} {_localized_usage(self, ctx)}")
        if self.help:
            formatter.write_paragraph()
            formatter.write_text(self.help)
        _format_parameters(self, ctx, formatter)
        if isinstance(self, TyperGroup):
            self._format_chinese_commands(ctx, formatter)

    def format_usage(self, ctx: Any, formatter: Any) -> None:
        formatter.write_text(f"用法: {ctx.command_path} {_localized_usage(self, ctx)}")

    def parse_args(self, ctx: Any, args: list[str]) -> list[str]:
        try:
            return super().parse_args(ctx, args)
        except _click.exceptions.UsageError as error:
            raise _ChineseUsageError(_translate_usage_error(error.format_message()), ctx) from error


class ChineseTyperCommand(_ChineseHelpMixin, TyperCommand):
    """Typer command whose help headings are Chinese."""


class ChineseTyperGroup(_ChineseHelpMixin, TyperGroup):
    """Typer group whose command listing heading is Chinese."""

    def _format_chinese_commands(self, ctx: Any, formatter: Any) -> None:
        rows = []
        for command_name in self.list_commands(ctx):
            command = self.get_command(ctx, command_name)
            if command is not None and not command.hidden:
                rows.append((command_name, command.get_short_help_str()))
        if rows:
            with formatter.section("命令"):
                formatter.write_dl(rows)


QUARTO_UNAVAILABLE = "未检测到 Quarto，无法渲染 PDF"
QMD_MISSING = "缺少 Quarto 源文件，无法渲染 PDF"
