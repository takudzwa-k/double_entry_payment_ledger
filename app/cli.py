import sys
import uuid

from app.commands import handle_message


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("Usage: python -m app.cli <phone_handle>", file=sys.stderr)
        return 1

    sender_handle = args[0]
    print(f"SMS simulator for {sender_handle}. Type HELP for commands, 'quit' or Ctrl+D to exit.")

    while True:
        try:
            line = input("> ")
        except EOFError:
            print()
            break

        if not sys.stdin.isatty():
            print(line)

        if line.strip().lower() == "quit":
            break

        reply = handle_message(sender_handle, line, str(uuid.uuid4()))
        print(reply)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
