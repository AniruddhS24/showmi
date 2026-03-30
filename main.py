import argparse
import asyncio


def cli():
    parser = argparse.ArgumentParser(
        description="Local browser automation agent",
        usage="python main.py [--confirm] TASK",
    )
    parser.add_argument("task", help="Task description for the agent")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Require confirmation before each agent step",
    )
    args = parser.parse_args()

    if args.confirm:
        import config
        object.__setattr__(config.config, "require_confirmation", True)

    from agent import run_agent

    asyncio.run(run_agent(args.task))


if __name__ == "__main__":
    cli()
