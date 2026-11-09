"""로컬 관리자 계정의 비밀번호를 안전하게 다시 설정하는 터미널 도구입니다.

비밀번호는 화면에 표시하지 않고 입력받으며, 원문을 파일이나 DB에 저장하지 않습니다.
"""

from getpass import getpass

from sqlalchemy import delete, select

from app import models
from app.auth import hash_password
from app.database import SessionLocal


def choose_user(users: list[models.User]) -> models.User:
    """계정이 하나면 바로 선택하고, 여러 개면 번호로 선택합니다."""
    print("\n등록된 계정")
    for index, user in enumerate(users, start=1):
        print(f"  {index}. {user.email} ({user.display_name})")
    if len(users) == 1:
        return users[0]
    while True:
        answer = input("비밀번호를 바꿀 계정 번호: ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(users):
            return users[int(answer) - 1]
        print("목록에 있는 번호를 입력해 주세요.")


def main() -> None:
    with SessionLocal() as session:
        users = list(session.scalars(select(models.User).order_by(models.User.created_at)))
        if not users:
            print("등록된 계정이 없습니다. 사이트에서 첫 관리자 계정을 만들어 주세요.")
            return
        user = choose_user(users)
        while True:
            password = getpass("새 비밀번호(12자 이상, 화면에 표시되지 않음): ")
            if len(password) < 12:
                print("비밀번호는 12자 이상이어야 합니다.")
                continue
            confirmation = getpass("새 비밀번호 다시 입력: ")
            if password != confirmation:
                print("두 비밀번호가 다릅니다. 다시 입력해 주세요.")
                continue
            break

        user.password_hash = hash_password(password)
        # 분실된 로그인 쿠키도 더는 사용할 수 없도록 이 계정의 기존 세션을 종료합니다.
        session.execute(delete(models.LoginSession).where(models.LoginSession.user_id == user.id))
        session.commit()
        print(f"\n완료: {user.email} 계정의 비밀번호를 변경했습니다.")
        print("브라우저에서 이 이메일과 새 비밀번호로 다시 로그인하세요.")


if __name__ == "__main__":
    main()
