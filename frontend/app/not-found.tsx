// 존재하지 않는 주소에 접근했을 때 보여 주는 404 안내 화면입니다.
import Link from "next/link";

export default function NotFound() {
  return <section className="panel empty-panel"><h1>페이지를 찾지 못했어요</h1><p>현재 제공되는 메뉴에서 이동해 주세요.</p><Link className="primary-button link-button" href="/">대시보드로 이동</Link></section>;
}
