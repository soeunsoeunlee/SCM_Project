"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import styles from "./dashboard.module.css";

const metrics = [
  { label: "진행 중 빌드", value: "12", change: "+3", tone: "lime" },
  { label: "이번 달 완료", value: "38", change: "+18%", tone: "indigo" },
  { label: "검토 대기", value: "7", change: "2건 긴급", tone: "amber" },
  { label: "평균 리드타임", value: "4.2일", change: "-0.8일", tone: "dark" },
] as const;

const initialTasks = [
  { id: 1, label: "튀르키예 판매계획 검토 행 확인", meta: "오늘 · 데이터 검증", done: true },
  { id: 2, label: "11월 생산계획 매칭 결과 검토", meta: "오늘 · Sales Master", done: false },
  { id: 3, label: "선적 ETD 변경분 담당자 공유", meta: "내일 · 선적정보", done: false },
  { id: 4, label: "PSI 신규 버전 승인 준비", meta: "11월 9일 · PSI", done: false },
];

const activity = [
  { mark: "UP", title: "판매계획 파일이 등록되었습니다", detail: "튀르키예 · 8개 주문 · 12분 전", color: "lime" },
  { mark: "PR", title: "생산정보 6ea가 주문에 연결되었습니다", detail: "구미 · SO-2026-003 · 48분 전", color: "indigo" },
  { mark: "SH", title: "선적 ETD가 변경되었습니다", detail: "Vessel Aurora · 2시간 전", color: "amber" },
  { mark: "OK", title: "ERP Closed 확인이 완료되었습니다", detail: "걸프 · 10ea · 어제", color: "green" },
];

const stages = [
  { label: "자료 등록", count: 18, percent: 88 },
  { label: "표준화", count: 15, percent: 74 },
  { label: "매칭 검토", count: 9, percent: 52 },
  { label: "Closed", count: 6, percent: 34 },
];

export default function BuildLogDashboardPage() {
  const [tasks, setTasks] = useState(initialTasks);
  const completed = useMemo(() => tasks.filter((task) => task.done).length, [tasks]);

  function toggleTask(id: number) {
    setTasks((current) => current.map((task) => task.id === id ? { ...task, done: !task.done } : task));
  }

  return (
    <div className={styles.dashboard}>
      <section className={styles.hero}>
        <div>
          <span className={styles.eyebrow}>BUILDLOG · OPERATIONS OVERVIEW</span>
          <h1>좋은 아침이에요, 이소은님</h1>
          <p>업로드부터 매칭, 선적과 Closed까지 오늘 확인할 흐름을 한곳에 모았습니다.</p>
        </div>
        <div className={styles.heroActions}>
          <span className={styles.today}>2026.11.06 · 금요일</span>
          <Link href="/uploads" className={styles.secondaryButton}>자료 업로드</Link>
          <Link href="/sales-master" className={styles.primaryButton}>Sales Master 열기</Link>
        </div>
      </section>

      <section className={styles.metricGrid} aria-label="주요 지표">
        {metrics.map((metric) => (
          <article className={`${styles.metricCard} ${styles[metric.tone]}`} key={metric.label}>
            <div className={styles.metricTop}>
              <span>{metric.label}</span>
              <i aria-hidden="true">↗</i>
            </div>
            <strong>{metric.value}</strong>
            <small>{metric.change}</small>
          </article>
        ))}
      </section>

      <section className={styles.contentGrid}>
        <article className={`${styles.card} ${styles.pipelineCard}`}>
          <div className={styles.cardHeading}>
            <div>
              <span className={styles.sectionKicker}>THIS MONTH</span>
              <h2>업무 처리 흐름</h2>
              <p>단계별 처리 건수와 현재 진행률입니다.</p>
            </div>
            <Link href="/sales-master" className={styles.textLink}>전체 주문 보기 →</Link>
          </div>
          <div className={styles.stageList}>
            {stages.map((stage) => (
              <div className={styles.stageRow} key={stage.label}>
                <div className={styles.stageLabel}>
                  <span>{stage.label}</span>
                  <strong>{stage.count}건</strong>
                </div>
                <div className={styles.track} aria-label={`${stage.label} ${stage.percent}%`}>
                  <span style={{ width: `${stage.percent}%` }} />
                </div>
                <b>{stage.percent}%</b>
              </div>
            ))}
          </div>
          <div className={styles.pipelineFoot}>
            <div><strong>68%</strong><span>전체 진행률</span></div>
            <p>지난주보다 <b>12%</b> 빠르게 처리되고 있습니다.</p>
          </div>
        </article>

        <article className={`${styles.card} ${styles.todoCard}`}>
          <div className={styles.cardHeading}>
            <div>
              <span className={styles.sectionKicker}>MY CHECKLIST</span>
              <h2>오늘 할 일</h2>
            </div>
            <span className={styles.taskCount}>{completed}/{tasks.length}</span>
          </div>
          <div className={styles.taskList}>
            {tasks.map((task) => (
              <button
                type="button"
                className={`${styles.task} ${task.done ? styles.taskDone : ""}`}
                aria-pressed={task.done}
                onClick={() => toggleTask(task.id)}
                key={task.id}
              >
                <span className={styles.check}>{task.done ? "✓" : ""}</span>
                <span><strong>{task.label}</strong><small>{task.meta}</small></span>
              </button>
            ))}
          </div>
          <p className={styles.localNote}>체크 상태는 이 화면에서만 유지됩니다.</p>
        </article>
      </section>

      <section className={styles.lowerGrid}>
        <article className={`${styles.card} ${styles.activityCard}`}>
          <div className={styles.cardHeading}>
            <div><span className={styles.sectionKicker}>LIVE LOG</span><h2>최근 활동</h2></div>
            <Link href="/history" className={styles.textLink}>변경 이력 →</Link>
          </div>
          <div className={styles.activityList}>
            {activity.map((item) => (
              <div className={styles.activityItem} key={item.title}>
                <span className={`${styles.activityMark} ${styles[item.color]}`}>{item.mark}</span>
                <div><strong>{item.title}</strong><small>{item.detail}</small></div>
                <i aria-hidden="true">›</i>
              </div>
            ))}
          </div>
        </article>

        <article className={`${styles.card} ${styles.alertCard}`}>
          <div className={styles.alertIcon}>!</div>
          <span className={styles.sectionKicker}>NEEDS ATTENTION</span>
          <h2>7건의 매칭 검토가 필요해요</h2>
          <p>PO와 모델 정보가 모호한 건은 자동 확정하지 않고 검토함에 보관했습니다.</p>
          <Link href="/sales-master" className={styles.darkButton}>검토할 주문 보기 <span>→</span></Link>
          <div className={styles.alertMeta}><span>생산정보 4건</span><span>선적정보 3건</span></div>
        </article>

        <article className={`${styles.card} ${styles.scheduleCard}`}>
          <div className={styles.cardHeading}>
            <div><span className={styles.sectionKicker}>UPCOMING</span><h2>이번 주 일정</h2></div>
            <Link href="/psi" className={styles.textLink}>PSI 보기 →</Link>
          </div>
          <div className={styles.scheduleList}>
            <div><time>07<small>NOV</small></time><p><strong>구미 PST 확정</strong><span>생산계획 · 24ea</span></p></div>
            <div><time>09<small>NOV</small></time><p><strong>PSI 주간 검토</strong><span>DM / SA / SP 비교</span></p></div>
            <div><time>11<small>NOV</small></time><p><strong>Vessel Aurora ETD</strong><span>선적정보 · 10ea</span></p></div>
          </div>
        </article>
      </section>
    </div>
  );
}
