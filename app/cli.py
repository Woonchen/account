"""帳號管理：python -m app.cli add-user | passwd | del-user | list-users

密碼用 getpass 輸入兩次（不會顯示、不會進紀錄），至少 10 個字。
"""
import argparse
import getpass
import sys

from . import auth, db


def ask_password():
    p1 = getpass.getpass("密碼（至少 10 個字）：")
    if len(p1) < 10:
        sys.exit("密碼太短")
    if getpass.getpass("再輸入一次：") != p1:
        sys.exit("兩次輸入不一樣")
    return p1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add-user", help="新增帳號")
    add.add_argument("username")
    add.add_argument("display_name", nargs="?", help="畫面上顯示的名字（預設同帳號）")
    sub.add_parser("passwd", help="改密碼（舊的登入會失效）").add_argument("username")
    sub.add_parser("del-user", help="刪除沒有記過帳的帳號").add_argument("username")
    sub.add_parser("list-users", help="列出帳號")
    args = ap.parse_args(argv)

    conn = db.connect()
    db.init(conn)
    exists = args.cmd != "list-users" and conn.execute(
        "SELECT 1 FROM users WHERE username = ?", (args.username,)).fetchone()

    if args.cmd == "add-user":
        if exists:
            sys.exit("帳號已經存在")
        conn.execute("INSERT INTO users (username, display_name, pass_hash) VALUES (?, ?, ?)",
                     (args.username, args.display_name or args.username, auth.make_hash(ask_password())))
        print(f"已新增 {args.username}")
    elif args.cmd == "passwd":
        if not exists:
            sys.exit("沒有這個帳號")
        conn.execute("UPDATE users SET pass_hash = ? WHERE username = ?", (auth.make_hash(ask_password()), args.username))
        print(f"已更新 {args.username} 的密碼")
    elif args.cmd == "del-user":
        if not exists:
            sys.exit("沒有這個帳號")
        try:
            conn.execute("DELETE FROM users WHERE username = ?", (args.username,))
        except db.sqlite3.IntegrityError:
            sys.exit("這個帳號記過帳，不能刪（可以改密碼讓它無法登入）")
        print(f"已刪除 {args.username}")
    else:
        for r in conn.execute("SELECT username, display_name, created_at FROM users ORDER BY id"):
            print(f"{r['username']}\t{r['display_name']}\t{r['created_at']}")
    conn.commit()


if __name__ == "__main__":
    main()
