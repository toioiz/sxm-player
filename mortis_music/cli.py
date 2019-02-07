# -*- coding: utf-8 -*-

"""Console script for mortis_music."""
import logging
import os
import signal
import time
from multiprocessing import Manager, Pool, set_start_method
from typing import Optional, Type

import click

from .models import XMState
from .runners import (
    ArchiveRunner,
    BaseRunner,
    BotRunner,
    HLSRunner,
    ProcessorRunner,
    ServerRunner,
    run,
)
from .utils import CustomCommandClass, configure_root_logger


@click.command(cls=CustomCommandClass)
@click.option(
    "--username", type=str, envvar="SXM_USERNAME", help="SiriusXM Username"
)
@click.option(
    "--password", type=str, envvar="SXM_PASSWORD", help="SiriusXM Password"
)
@click.option(
    "-r",
    "--region",
    type=click.Choice(["US", "CA"]),
    default="US",
    help="Sets the SiriusXM client's region",
)
@click.option(
    "--token", type=str, envvar="DISCORD_TOKEN", help="Discord bot token"
)
@click.option(
    "--prefix", type=str, default="/music ", help="Discord bot command prefix"
)
@click.option(
    "--description",
    type=str,
    default="SiriusXM radio bot for Discord",
    help="port to run SiriusXM Proxy server on",
)
@click.option(
    "-p",
    "--port",
    type=int,
    default=9999,
    help="port to run SiriusXM Proxy server on",
)
@click.option(
    "-h",
    "--host",
    type=str,
    default="127.0.0.1",
    help="IP to bind SiriusXM Proxy server to. "
    "Must still be accessible via 127.0.0.1",
)
@click.option(
    "-o",
    "--output-folder",
    type=click.Path(),
    default=None,
    envvar="MUSIC_OUTPUT_FOLDER",
    help="output folder to save stream off to as it plays them",
)
@click.option(
    "-r", "--reset-songs", is_flag=True, help="reset processed song database"
)
@click.option(
    "--plex-username",
    type=str,
    default=None,
    envvar="PLEX_USERNAME",
    help="Plex username for local server",
)
@click.option(
    "--plex-password",
    type=str,
    default=None,
    envvar="PLEX_PASSWORD",
    help="Plex password for local server",
)
@click.option(
    "--plex-server-name",
    type=str,
    default=None,
    envvar="PLEX_SERVER",
    help="Plex server name for local server",
)
@click.option(
    "--plex-library-name",
    type=str,
    default=None,
    envvar="PLEX_LIBRARY",
    help="Plex library name for local server",
)
@click.option(
    "-l", "--log-file", type=click.Path(), default=None, help="output log file"
)
@click.option(
    "--config-file", type=click.Path(), help="Config file to read vars from"
)
@click.option("-d", "--debug", is_flag=True, help="enable debug logging")
def main(
    username: str,
    password: str,
    region: str,
    token: str,
    prefix: str,
    description: str,
    port: int,
    host: str,
    output_folder: str,
    reset_songs: bool,
    debug: bool,
    log_file: str,
    plex_username: str,
    plex_password: str,
    plex_server_name: str,
    plex_library_name: str,
    config_file: str,
):
    """Command line interface for SiriusXM radio bot for Discord"""

    context = click.get_current_context()
    if username is None:
        raise click.BadParameter(
            "SiriusXM Username is required",
            ctx=context,
            param=context.params.get("username"),
        )
    elif password is None:
        raise click.BadParameter(
            "SiriusXM Password is required",
            ctx=context,
            param=context.params.get("password"),
        )
    elif token is None:
        raise click.BadParameter(
            "Discord Token is required",
            ctx=context,
            param=context.params.get("token"),
        )

    level = "INFO"

    if debug:
        set_start_method("spawn")
        level = "DEBUG"

    configure_root_logger(level, log_file)
    os.system("clear")

    with Manager() as manager:
        state_dict = manager.dict()  # type: ignore
        XMState.init_state(state_dict)
        lock = manager.Lock()  # type: ignore # pylint: disable=E1101 # noqa
        state = XMState(state_dict, lock)
        logger = logging.getLogger("mortis_music")

        process_count = 3
        if output_folder is not None:
            state.output = output_folder
            process_count = 5

        def init_worker():
            signal.signal(signal.SIGINT, signal.SIG_IGN)

        def is_running(name):
            try:
                os.kill(state.runners[name], 0)
                return True
            except (KeyError, TypeError, ProcessLookupError):
                state.set_runner(name, None)
                return False

        if debug:
            init_worker = None  # type: ignore # noqa

        with Pool(processes=process_count, initializer=init_worker) as pool:

            base_url = f"http://{host}:{port}"

            def spawn_process(
                cls: Type[BaseRunner], kwargs: Optional[dict] = None
            ):
                if kwargs is None:
                    kwargs = {}

                pool.apply_async(
                    func=run,
                    args=(cls, state_dict, lock, level, log_file),
                    kwds=kwargs,
                )

            try:
                while True:
                    delay = 0.1

                    if not is_running("server"):
                        spawn_process(
                            ServerRunner,
                            {
                                "port": port,
                                "ip": host,
                                "username": username,
                                "password": password,
                                "region": region,
                            },
                        )
                        delay = 5

                    if not is_running("bot"):
                        spawn_process(
                            BotRunner,
                            {
                                "prefix": prefix,
                                "description": description,
                                "token": token,
                                "plex_username": plex_username,
                                "plex_password": plex_password,
                                "plex_server_name": plex_server_name,
                                "plex_library_name": plex_library_name,
                            },
                        )
                        delay = 5

                    if output_folder is not None:
                        if not is_running("archiver"):
                            spawn_process(ArchiveRunner)
                            delay = 5

                        if not is_running("processor"):
                            spawn_process(
                                ProcessorRunner, {"reset_songs": reset_songs}
                            )
                            delay = 5

                    if state.active_channel_id is not None:
                        if not is_running("hls"):
                            spawn_process(
                                HLSRunner, {"base_url": base_url, "port": port}
                            )
                            delay = 5

                    time.sleep(delay)

            except KeyboardInterrupt:
                pass
            finally:
                logger.warn("killing runners")
                pool.close()
                pool.terminate()
                pool.join()
    return 0
