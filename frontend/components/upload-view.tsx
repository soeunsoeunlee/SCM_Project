"use client";

// 구조화 파일과 메일·이미지·텍스트 원본을 단계별로 업로드·검증하는 화면입니다.

import { FormEvent, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { createCompany, getDashboard } from "@/lib/api";
import {
  applyUpload,
  deleteUploadMaterial,
  excludeUploadRow,
  getMapping,
  getUploadValidation,
  getUploadHistory,
  inspectUnstructured,
  inspectUpload,
  InspectResult,
  MappingContext,
  prepareUpload,
  PrepareResult,
  reopenUpload,
  UnstructuredResult,
  UploadHistoryItem,
  ValidationRow,
} from "@/lib/upload-api";

const number = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 2 });
const extractedLabels: Record<string, string> = {
  po_number: "PO번호", po_line_number: "PO라인", model: "모델", customer: "고객",
  quantity: "수량", net_sales_usd: "Net Sales (USD)", gross_sales_usd: "Gross Sales (USD)",
  sales_company: "판매법인", production_company: "생산법인", production_site: "생산사이트",
  sales_region: "판매지역", target_month: "대상 월", pst: "PST", production_status: "생산 상태",
  etd: "ETD", carrier: "선사", vessel_name: "선박", shipment_status: "선적 상태",
};

export function UploadView() {
  // 각 단계의 서버 응답을 별도 상태에 보관해 이전 단계도 화면에서 확인합니다.
  const [companies, setCompanies] = useState<
    { id: string; name: string; kind: string }[]
  >([]);
  const [history, setHistory] = useState<UploadHistoryItem[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [meta, setMeta] = useState({
    data_type: "판매계획",
    sales_company_id: "sales-a",
    production_company_id: "plant-b",
    target_month: new Date().toISOString().slice(0, 7),
    reference_date: new Date().toISOString().slice(0, 10),
    scope_type: "전체 현황",
    scope_description: "선택 법인의 해당 월 전체 자료",
  });
  const [newCompany, setNewCompany] = useState<{
    kind: "판매법인" | "생산법인";
    name: string;
  }>({ kind: "판매법인", name: "" });
  const [inspection, setInspection] = useState<InspectResult | null>(null);
  const [sheetName, setSheetName] = useState("");
  const [headerRow, setHeaderRow] = useState(1);
  const [mappingContext, setMappingContext] = useState<MappingContext | null>(
    null,
  );
  const [mapping, setMapping] = useState<Record<string, number>>({});
  const [pricing, setPricing] = useState<Record<string, string>>({
    net_sales: "",
    gross_sales: "",
  });
  const [validation, setValidation] = useState<PrepareResult | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [unstructured, setUnstructured] = useState<UnstructuredResult | null>(
    null,
  );
  const [unstructuredType, setUnstructuredType] = useState("생산정보");

  useEffect(() => {
    // 처음 열릴 때 판매법인과 현재 프로젝트의 실제 업로드 이력을 가져옵니다.
    void Promise.all([getDashboard(), getUploadHistory()])
      .then(([dashboard, historyRows]) => {
        setCompanies(dashboard.companies);
        setHistory(historyRows);
      })
      .catch((reason) =>
        setError(
          reason instanceof Error
            ? reason.message
            : "초기 정보를 불러오지 못했습니다.",
        ),
      );
  }, []);

  const selectedSheet = useMemo(
    () => inspection?.sheets.find((sheet) => sheet.name === sheetName) ?? null,
    [inspection, sheetName],
  );
  const salesCompanies = useMemo(
    () => companies.filter((company) => company.kind === "판매법인"),
    [companies],
  );
  const productionCompanies = useMemo(() => {
    const priority: Record<string, number> = {
      이집트: 0,
      인도네시아: 1,
      구미: 2,
    };
    return companies
      .filter((company) => company.kind === "생산법인")
      .sort(
        (left, right) =>
          (priority[left.name] ?? 99) - (priority[right.name] ?? 99) ||
          left.name.localeCompare(right.name, "ko"),
      );
  }, [companies]);

  async function addCompany() {
    const name = newCompany.name.trim();
    if (!name) {
      setError("추가할 법인명을 입력해 주세요.");
      return;
    }
    setBusy("company");
    setError("");
    try {
      const saved = await createCompany({
        name,
        kind: newCompany.kind,
        country: name,
      });
      setCompanies((current) => [...current, saved]);
      setMeta((current) => ({
        ...current,
        ...(saved.kind === "판매법인" && current.data_type === "판매계획"
          ? { sales_company_id: saved.id }
          : {}),
        ...(saved.kind === "생산법인" && current.data_type !== "판매계획"
          ? { production_company_id: saved.id }
          : {}),
      }));
      setNewCompany((current) => ({ ...current, name: "" }));
      setMessage(`${saved.name} ${saved.kind}을 현재 프로젝트에 추가했습니다.`);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "법인을 추가하지 못했습니다.",
      );
    } finally {
      setBusy("");
    }
  }

  async function inspect(event: FormEvent<HTMLFormElement>) {
    // 1단계: 파일과 적용 범위를 보내 원본을 보존하고 시트 미리보기를 받습니다.
    event.preventDefault();
    if (!file) {
      setError("업로드할 파일을 선택해 주세요.");
      return;
    }
    setBusy("inspect");
    setError("");
    setMessage("");
    setValidation(null);
    setMappingContext(null);
    try {
      const form = new FormData();
      form.append("file", file);
      Object.entries(meta).forEach(([key, value]) => form.append(key, value));
      const result = await inspectUpload(form);
      setInspection(result);
      const preferred =
        result.sheets.find((sheet) => !/안내|read\s*me/i.test(sheet.name)) ??
        result.sheets[0];
      setSheetName(preferred?.name ?? "");
      setHeaderRow(result.batch.header_row ?? 1);
      setMessage(result.message);
      setHistory(await getUploadHistory());
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "파일을 검사하지 못했습니다.",
      );
    } finally {
      setBusy("");
    }
  }

  async function confirmHeader() {
    // 2단계: 헤더의 별칭 제안 또는 저장된 법인별 매핑을 받습니다.
    if (!inspection || !sheetName) return;
    setBusy("mapping");
    setError("");
    setValidation(null);
    try {
      const context = await getMapping(
        inspection.batch.id,
        sheetName,
        headerRow,
      );
      setMappingContext(context);
      const nextMapping = context.saved_mapping ?? context.suggested_mapping;
      setMapping(nextMapping);
      setPricing(context.saved_pricing ?? { net_sales: "", gross_sales: "" });
      setMessage(context.message);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "헤더를 확인하지 못했습니다.",
      );
    } finally {
      setBusy("");
    }
  }

  async function openSalesMatching(item: UploadHistoryItem) {
    setBusy(`match-${item.id}`);
    setError("");
    setMessage("");
    setValidation(null);
    setMappingContext(null);
    try {
      const result = await reopenUpload(item.id);
      setInspection(result);
      const preferred =
        result.sheets.find((sheet) => !/안내|read\s*me/i.test(sheet.name)) ??
        result.sheets[0];
      setSheetName(result.batch.selected_sheet ?? preferred?.name ?? "");
      setHeaderRow(result.batch.header_row ?? 1);
      if (result.batch.selected_sheet && result.batch.header_row) {
        try {
          setValidation(await getUploadValidation(item.id));
        } catch {
          setValidation(null);
        }
      }
      setMessage(result.message);
      window.setTimeout(
        () =>
          document
            .getElementById("sales-master-matching")
            ?.scrollIntoView({ behavior: "smooth", block: "start" }),
        50,
      );
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "판매계획을 다시 불러오지 못했습니다.",
      );
    } finally {
      setBusy("");
    }
  }

  async function validate() {
    // 3단계: 수동 매핑과 단가/총액 설정으로 모든 행을 표준화·검증합니다.
    if (!inspection || !mappingContext) return;
    setBusy("validate");
    setError("");
    try {
      const result = await prepareUpload(inspection.batch.id, {
        sheet_name: sheetName,
        header_row: headerRow,
        mapping,
        pricing,
      });
      setValidation(result);
      setMessage(result.batch.result_message ?? "표준화 결과를 만들었습니다.");
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "표준화 처리에 실패했습니다.",
      );
    } finally {
      setBusy("");
    }
  }

  async function apply() {
    // 4단계: 오류·검토가 없는 배치만 명시적 클릭으로 최종 반영합니다.
    if (!inspection || !validation) return;
    setBusy("apply");
    setError("");
    try {
      const result = await applyUpload(inspection.batch.id);
      setInspection({ ...inspection, batch: result.batch });
      setMessage(
        `${result.message}${result.duplicate_request ? " (중복 클릭 방지됨)" : ""}`,
      );
      setValidation({ ...validation, batch: result.batch, can_apply: false });
      setHistory(await getUploadHistory());
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "반영에 실패했습니다.",
      );
    } finally {
      setBusy("");
    }
  }

  async function excludeRow(row: ValidationRow) {
    if (!inspection) return;
    const confirmed = window.confirm(
      `${row.row_number}행을 이번 반영에서 제외하시겠습니까?\n\n제외하면 이 행은 Sales Master에 저장되지 않습니다. 원본 파일과 제외 사유는 이력에 보존됩니다.`,
    );
    if (!confirmed) return;
    setBusy(`exclude-row-${row.id}`);
    setError("");
    try {
      const result = await excludeUploadRow(inspection.batch.id, row.id);
      setValidation(result);
      setInspection((current) => current ? { ...current, batch: result.batch } : current);
      setMessage(result.batch.result_message ?? `${row.row_number}행을 제외했습니다.`);
      setHistory(await getUploadHistory());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "행을 제외하지 못했습니다.");
    } finally {
      setBusy("");
    }
  }

  async function uploadUnstructured(event: FormEvent<HTMLFormElement>) {
    // 메일·텍스트·이미지 원본을 저장하고 가능한 텍스트 추출 결과를 받습니다.
    event.preventDefault();
    setBusy("unstructured");
    setError("");
    setUnstructured(null);
    try {
      const form = new FormData(event.currentTarget);
      const result = await inspectUnstructured(form);
      setUnstructured(result);
      setMessage(result.message);
      setHistory(await getUploadHistory());
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "비정형 자료를 처리하지 못했습니다.",
      );
    } finally {
      setBusy("");
    }
  }

  async function removeHistoryItem(item: UploadHistoryItem) {
    const salesPlanNotice =
      item.data_type === "판매계획"
        ? "\n\n이 파일로 생성된 Sales Master 주문과 연결된 생산·선적 상세, ERP Closed, 수동 수정값, 변경 이력도 모두 함께 영구 삭제됩니다."
        : "";
    const confirmed = window.confirm(
      `삭제하면 복구할 수 없습니다.\n\n파일명: ${item.file_name}\n자료 유형: ${item.data_type}\n기준일: ${item.reference_date ?? "없음"}${salesPlanNotice}`,
    );
    if (!confirmed) return;
    setBusy(`delete-${item.id}`);
    setError("");
    setMessage("");
    try {
      const result = await deleteUploadMaterial(item.record_kind, item.id);
      if (inspection?.batch.id === item.id) {
        setInspection(null);
        setMappingContext(null);
        setValidation(null);
      }
      if (unstructured?.id === item.id) setUnstructured(null);
      setHistory(await getUploadHistory());
      setMessage(result.message);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "업로드 자료를 삭제하지 못했습니다.",
      );
    } finally {
      setBusy("");
    }
  }

  return (
    <>
      <section className="page-heading">
        <div>
          <div className="breadcrumb">
            <Link href="/">업무 현황</Link>
            <span>/</span>자료 업로드
          </div>
          <div className="heading-line">
            <h1>자료 업로드</h1>
            <span className="status-chip">● 업무 자료 관리</span>
          </div>
          <p className="heading-subtitle">
            판매계획, 생산정보와 선적정보 파일을 프로젝트에 등록하고 원본 이력을
            관리합니다.
          </p>
        </div>
      </section>

      {error && (
        <div className="error-banner" role="alert">
          <span>{error}</span>
          <button className="small-button" onClick={() => setError("")}>
            닫기
          </button>
        </div>
      )}
      {message && (
        <div className="success-banner" role="status">
          ✓ {message}
        </div>
      )}

      <form className="panel upload-panel" onSubmit={inspect}>
        <div className="panel-heading">
          <div>
            <h2>파일 업로드</h2>
            <p>XLSX, XLS, CSV · 최대 10MB</p>
          </div>
        </div>
        <fieldset className="data-type-choice main-type-choice">
          <legend>자료 유형</legend>
          <div>
            {["판매계획", "생산정보", "선적정보"].map((type) => (
              <label
                className={meta.data_type === type ? "selected" : ""}
                key={type}
              >
                <input
                  type="radio"
                  name="data_type_choice"
                  value={type}
                  checked={meta.data_type === type}
                  onChange={() => {
                    const operational = type !== "판매계획";
                    setMeta({
                      ...meta,
                      data_type: type,
                      sales_company_id: operational
                        ? ""
                        : meta.sales_company_id || salesCompanies[0]?.id || "",
                      production_company_id: operational
                        ? meta.production_company_id ||
                          productionCompanies[0]?.id ||
                          ""
                        : "",
                      scope_description: operational
                        ? `선택 생산법인의 여러 판매법인 ${type}`
                        : "선택 판매법인의 해당 월 판매계획",
                    });
                    setInspection(null);
                    setMappingContext(null);
                    setValidation(null);
                  }}
                />
                <strong>{type}</strong>
                <span>
                  {type === "판매계획"
                    ? "주문·수량·매출계획"
                    : type === "생산정보"
                      ? "PST·생산수량·완료정보"
                      : "ETD·선사·선박"}
                </span>
              </label>
            ))}
          </div>
          <p>
            생산·선적정보는 파일 안의 여러 판매법인을 함께 처리하고 생산법인만
            선택합니다. 한 파일에 두 정보가 함께 있으면 각 유형으로 한 번씩
            업로드하세요.
          </p>
        </fieldset>
        <div className="upload-meta-grid">
          <label className="file-field">
            파일 선택
            <input
              type="file"
              accept=".xlsx,.xls,.csv"
              required
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
            <span>{file?.name ?? "파일을 선택하세요"}</span>
          </label>
          {meta.data_type !== "판매계획" ? (
            <label>
              판매법인
              <input
                value="파일 내 여러 판매법인"
                disabled
                aria-label="생산·선적정보의 판매법인 범위"
              />
              <span>파일에 포함된 판매법인을 함께 처리합니다.</span>
            </label>
          ) : (
            <label>
              판매법인
              <select
                required
                value={meta.sales_company_id}
                onChange={(event) =>
                  setMeta({ ...meta, sales_company_id: event.target.value })
                }
              >
                {salesCompanies.map((company) => (
                  <option key={company.id} value={company.id}>
                    {company.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label>
            대상 매출 월 · 자동 반영
            <input
              type="month"
              value={meta.target_month}
              disabled
              aria-label="자료 기준일에서 자동 계산된 대상 매출 월"
            />
            <span>자료 기준일과 같은 연·월로 자동 저장됩니다.</span>
          </label>
          <label>
            자료 기준일
            <input
              type="date"
              required
              value={meta.reference_date}
              onChange={(event) =>
                setMeta({
                  ...meta,
                  reference_date: event.target.value,
                  target_month: event.target.value.slice(0, 7),
                })
              }
            />
          </label>
          <label>
            자료 범위
            <select
              value={meta.scope_type}
              onChange={(event) =>
                setMeta({ ...meta, scope_type: event.target.value })
              }
            >
              <option>전체 현황</option>
              <option>변경분</option>
            </select>
          </label>
          <label className="scope-field">
            적용 범위
            <input
              required
              maxLength={300}
              value={meta.scope_description}
              onChange={(event) =>
                setMeta({ ...meta, scope_description: event.target.value })
              }
              placeholder="예: 걸프 10월 전체 모델"
            />
          </label>
        </div>
        {meta.data_type !== "판매계획" && (
          <fieldset className="upload-company-toggles">
            <legend>생산법인 선택</legend>
            <div>
              {productionCompanies.map((company) => (
                <label
                  className={
                    meta.production_company_id === company.id ? "selected" : ""
                  }
                  key={company.id}
                >
                  <input
                    type="radio"
                    name="production_company"
                    value={company.id}
                    checked={meta.production_company_id === company.id}
                    onChange={() =>
                      setMeta({ ...meta, production_company_id: company.id })
                    }
                  />
                  <strong>{company.name}</strong>
                </label>
              ))}
            </div>
          </fieldset>
        )}
        <div className="company-add-row">
          <strong>법인 직접 추가</strong>
          <select
            aria-label="추가할 법인 유형"
            value={newCompany.kind}
            onChange={(event) =>
              setNewCompany({
                ...newCompany,
                kind: event.target.value as "판매법인" | "생산법인",
              })
            }
          >
            <option>판매법인</option>
            <option>생산법인</option>
          </select>
          <input
            value={newCompany.name}
            maxLength={120}
            placeholder="새 법인명"
            onChange={(event) =>
              setNewCompany({ ...newCompany, name: event.target.value })
            }
          />
          <button
            type="button"
            className="small-button neutral"
            disabled={Boolean(busy)}
            onClick={() => void addCompany()}
          >
            {busy === "company" ? "추가 중…" : "추가"}
          </button>
          <span>현재 프로젝트에서만 사용됩니다.</span>
        </div>
        <div className="upload-actions">
          <p>
            {meta.data_type} ·{" "}
            {meta.data_type === "판매계획"
              ? (companies.find(
                  (company) => company.id === meta.sales_company_id,
                )?.name ?? "판매법인 선택")
              : `${companies.find((company) => company.id === meta.production_company_id)?.name ?? "생산법인 선택"} · 여러 판매법인`}{" "}
            · {meta.target_month}
          </p>
          <button className="primary-button" disabled={Boolean(busy)}>
            {busy === "inspect" ? "업로드 중…" : "파일 업로드"}
          </button>
        </div>
      </form>

      <section className="panel unstructured-panel">
        <div className="panel-heading">
          <div>
            <h2>메일·이미지·텍스트 입력</h2>
            <p>
              OpenAI API가 메일 파일, 화면 캡처, 메일 본문과 복사한 텍스트를 읽어 업무 항목으로 정리합니다.
            </p>
          </div>
        </div>
        <form onSubmit={uploadUnstructured}>
          <fieldset className="data-type-choice">
            <legend>자료 유형</legend>
            <div>
              {["판매계획", "생산정보", "선적정보"].map((type) => (
                <label
                  className={unstructuredType === type ? "selected" : ""}
                  key={type}
                >
                  <input
                    type="radio"
                    name="data_type"
                    value={type}
                    checked={unstructuredType === type}
                    onChange={() => setUnstructuredType(type)}
                  />
                  <strong>{type}</strong>
                  <span>
                    {type === "생산정보"
                      ? "PST·생산수량·생산완료"
                      : type === "선적정보"
                        ? "ETD·선사·선박"
                        : "주문·수요·계획"}
                  </span>
                </label>
              ))}
            </div>
            <p>
              한 내용에 생산과 선적이 함께 있으면 자료 유형을 바꾸어 각각 한
              번씩 등록하세요.
            </p>
          </fieldset>
          <div className="unstructured-grid">
            <label className="file-field">
              메일·이미지 파일
              <input
                name="file"
                type="file"
                accept=".eml,.html,.htm,.txt,.png,.jpg,.jpeg,.webp"
              />
              <span>EML, HTML, TXT, PNG, JPG, WEBP 파일 선택</span>
            </label>
            <label className="paste-field">
              메일 본문·텍스트 붙여넣기
              <textarea
                name="pasted_text"
                rows={7}
                placeholder={"메일 본문이나 업무 텍스트를 여기에 붙여넣으세요."}
              />
            </label>
          </div>
          <div className="upload-actions">
            <p>원본과 OpenAI 추출 결과를 함께 저장하며, 확인 전에는 Sales Master에 반영하지 않습니다.</p>
            <button className="primary-button" disabled={Boolean(busy)}>
              {busy === "unstructured"
                ? "등록 중…"
                : `${unstructuredType} 등록`}
            </button>
          </div>
        </form>
        {unstructured && (
          <div className="unstructured-result">
            <div>
              <strong>{unstructured.extraction_status}</strong>
              <span>
                {unstructured.data_type} · {unstructured.input_type} ·{" "}
                {unstructured.file_name ?? "붙여넣기 텍스트"}
              </span>
              <a
                href={`/api/backend/api/source-materials/${unstructured.source_id}/download`}
                download
              >
                원본 다운로드 ↓
              </a>
            </div>
            <div className="ai-extraction-result">
              <strong>OpenAI가 읽어낸 업무 항목</strong>
              {Array.isArray(unstructured.extracted_fields.records) ? (
                <>
                  <p>{String(unstructured.extracted_fields.document_summary ?? "")}</p>
                  <div className="ai-record-list">
                    {(unstructured.extracted_fields.records as Record<string, unknown>[]).map((record, index) => (
                      <article key={index}>
                        <b>항목 {index + 1} · 신뢰도 {String(record.confidence ?? "-")}</b>
                        <dl>
                          {Object.entries(record)
                            .filter(([key, value]) => Boolean(value) && !["confidence", "evidence"].includes(key))
                            .map(([key, value]) => (
                              <div key={key}><dt>{extractedLabels[key] ?? key}</dt><dd>{String(value)}</dd></div>
                            ))}
                        </dl>
                        {record.evidence ? <small>근거: {String(record.evidence)}</small> : null}
                      </article>
                    ))}
                  </div>
                  {Array.isArray(unstructured.extracted_fields.warnings) && unstructured.extracted_fields.warnings.length > 0 ? (
                    <p className="ai-warning">확인 필요: {(unstructured.extracted_fields.warnings as unknown[]).map(String).join(" · ")}</p>
                  ) : null}
                </>
              ) : Object.keys(unstructured.extracted_fields).length ? (
                <dl>
                  {Object.entries(unstructured.extracted_fields).map(([key, value]) => (
                    <div key={key}><dt>{extractedLabels[key] ?? key}</dt><dd>{String(value)}</dd></div>
                  ))}
                </dl>
              ) : <p>표시할 주요 항목이 없습니다.</p>}
            </div>
            <div>
              <strong>본문 미리보기</strong>
              <pre>{unstructured.extracted_text ?? "등록된 이미지 원본"}</pre>
            </div>
          </div>
        )}
      </section>

      <section className="panel upload-history-panel">
        <div className="panel-heading">
          <div>
            <h2>전체 업로드 이력</h2>
            <p>
              엑셀·CSV 파일과 메일·이미지·붙여넣기 텍스트를 최근 등록 순서로
              함께 확인합니다.
            </p>
          </div>
          <span className="history-count">{history.length}건</span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>등록일시</th>
                <th>등록자</th>
                <th>자료 유형</th>
                <th>입력 방식</th>
                <th>파일명</th>
                <th>판매법인</th>
                <th>생산법인</th>
                <th>대상 월</th>
                <th>기준일</th>
                <th>범위</th>
                <th>상태</th>
                <th>원본</th>
                <th>관리</th>
              </tr>
            </thead>
            <tbody>
              {history.map((item) => (
                <tr key={`${item.data_type}-${item.id}`}>
                  <td>{new Date(item.created_at).toLocaleString("ko-KR")}</td>
                  <td>{item.created_by ?? "—"}</td>
                  <td>
                    <span className="type-badge">{item.data_type}</span>
                  </td>
                  <td>
                    <strong>{item.input_type}</strong>
                  </td>
                  <td>{item.file_name}</td>
                  <td>{item.sales_company ?? "—"}</td>
                  <td>{item.production_company ?? "—"}</td>
                  <td>{item.target_month ?? "—"}</td>
                  <td>{item.reference_date ?? "—"}</td>
                  <td>{item.scope_type ?? "—"}</td>
                  <td>
                    <strong>{item.status}</strong>
                    {item.result_message && (
                      <span className="cell-secondary">{item.result_message}</span>
                    )}
                  </td>
                  <td>
                    <a
                      className="source-link"
                      href={`/api/backend${item.download_url}`}
                      download
                    >
                      원본 다운로드 ↓
                    </a>
                  </td>
                  <td>
                    <div className="history-row-actions">
                      {item.record_kind === "batch" &&
                        item.data_type === "판매계획" &&
                        !["반영 완료", "버전 비교 대기"].includes(
                          item.status,
                        ) && (
                          <button
                            type="button"
                            className="matching-button"
                            disabled={Boolean(busy)}
                            onClick={() => void openSalesMatching(item)}
                          >
                            {busy === `match-${item.id}`
                              ? "불러오는 중…"
                              : "Sales Master 매칭"}
                          </button>
                        )}
                      {item.record_kind === "batch" &&
                        item.data_type === "판매계획" &&
                        ["반영 완료", "버전 비교 대기"].includes(
                          item.status,
                        ) && (
                          <span className="cell-secondary">매칭 처리 완료</span>
                        )}
                      {item.deletable ? (
                        <button
                          type="button"
                          className="danger-button"
                          disabled={Boolean(busy)}
                          onClick={() => void removeHistoryItem(item)}
                        >
                          {busy === `delete-${item.id}` ? "삭제 중…" : "삭제"}
                        </button>
                      ) : (
                        <span className="cell-secondary">삭제 제외</span>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
              {history.length === 0 && (
                <tr>
                  <td className="loading-cell" colSpan={13}>
                    등록된 자료가 없습니다.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {inspection?.batch.data_type === "판매계획" && (
        <section className="panel sheet-panel" id="sales-master-matching">
          <div className="panel-heading">
            <div>
              <h2>판매계획 시트 선택</h2>
              <p>
                {inspection.batch.file_name} · {inspection.batch.status}
              </p>
            </div>
            {inspection.duplicate_file && (
              <span className="pill blue">
                <i />
                기존 파일 열기
              </span>
            )}
          </div>
          <div className="sheet-controls">
            <label>
              가져올 시트
              <select
                value={sheetName}
                onChange={(event) => {
                  setSheetName(event.target.value);
                  setMappingContext(null);
                  setValidation(null);
                }}
              >
                {inspection.sheets.map((sheet) => (
                  <option key={sheet.name}>{sheet.name}</option>
                ))}
              </select>
            </label>
            <label>
              헤더 행
              <input
                type="number"
                min="1"
                max={selectedSheet?.row_count ?? 10000}
                value={headerRow}
                onChange={(event) => {
                  setHeaderRow(Number(event.target.value));
                  setMappingContext(null);
                  setValidation(null);
                }}
              />
            </label>
            <button
              type="button"
              className="primary-button"
              disabled={Boolean(busy)}
              onClick={() => void confirmHeader()}
            >
              {busy === "mapping" ? "확인 중…" : "헤더와 매핑 확인"}
            </button>
          </div>
          {selectedSheet && (
            <>
              <div className="sheet-meta">
                {selectedSheet.name} · {number.format(selectedSheet.row_count)}
                행 · {number.format(selectedSheet.column_count)}열 · 선택한 헤더{" "}
                {headerRow}행
              </div>
              <div className="raw-preview">
                <table>
                  <tbody>
                    {selectedSheet.preview.map((row, rowIndex) => (
                      <tr
                        key={rowIndex}
                        className={
                          rowIndex + 1 === headerRow ? "header-choice" : ""
                        }
                      >
                        <th>{rowIndex + 1}</th>
                        {row.map((cell, cellIndex) => (
                          <td key={cellIndex}>
                            {cell === null || cell === undefined ? (
                              <span className="muted">(빈값)</span>
                            ) : (
                              String(cell)
                            )}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </section>
      )}

      {mappingContext && (
        <section className="panel mapping-panel">
          <div className="panel-heading">
            <div>
              <h2>판매계획 컬럼 연결</h2>
              <p>
                {mappingContext.profile_reused
                  ? "이 법인에서 사용한 컬럼 연결을 불러왔습니다."
                  : mappingContext.ai_status === "used"
                    ? "규칙으로 찾지 못한 항목에 AI 추천을 더했습니다. 사람이 한 번 확인해 주세요."
                    : "규칙 기반 추천을 표시했습니다. 사람이 한 번 확인해 주세요."}
              </p>
            </div>
            <span
              className={`pill ${mappingContext.profile_reused ? "green" : "blue"}`}
            >
              <i />
              {mappingContext.profile_reused
                ? "저장 설정 사용"
                : mappingContext.ai_status === "used"
                  ? "AI + 규칙 추천"
                  : "규칙 추천"}
            </span>
          </div>
          <div className="production-site-mapping">
            <strong>생산사이트 · 자동 매칭</strong>
            <p>
              판매계획에서는 생산사이트를 직접 지정하지 않습니다. 업로드된 생산정보의
              PO번호·모델명·수량을 비교해 후보가 하나뿐일 때 생산법인과 PST를 자동 연결합니다.
            </p>
          </div>
          <div className="mapping-grid">
            {mappingContext.standard_fields.map((field) => (
                <label key={field.key}>
                  <span>
                    {field.label}
                    {field.required && <b>필수</b>}
                  </span>
                  <select
                    value={mapping[field.key] ?? ""}
                    onChange={(event) => {
                      const next = { ...mapping };
                      if (event.target.value === "") delete next[field.key];
                      else next[field.key] = Number(event.target.value);
                      setMapping(next);
                    }}
                  >
                    <option value="">연결하지 않음</option>
                    {mappingContext.headers.map((header, index) => (
                      <option key={`${header}-${index}`} value={index}>
                        {index + 1}. {header}
                      </option>
                    ))}
                  </select>
                  {(field.key === "net_sales" || field.key === "gross_sales") &&
                    mapping[field.key] !== undefined && (
                      <select
                        aria-label={`${field.label} 가격 구분`}
                        value={pricing[field.key] ?? ""}
                        onChange={(event) =>
                          setPricing({
                            ...pricing,
                            [field.key]: event.target.value,
                          })
                        }
                      >
                        <option value="">단가/총액 선택</option>
                        <option>단가</option>
                        <option>총액</option>
                      </select>
                    )}
                </label>
              ))}
          </div>
          <div className="upload-actions">
            <p>
              PO와 모델명은 원본 형식을 유지합니다. 통화는 별도 연결 없이 USD로
              적용합니다.
            </p>
            <button
              type="button"
              className="primary-button"
              disabled={Boolean(busy)}
              onClick={() => void validate()}
            >
              {busy === "validate" ? "처리 중…" : "표준화 결과 만들기"}
            </button>
          </div>
        </section>
      )}

      {validation && (
        <section className="validation-area">
          <div className="validation-summary">
            <article>
              <span>사용</span>
              <strong>{validation.status_counts["처리"] ?? 0}</strong>
            </article>
            <article>
              <span>합계 등 제외</span>
              <strong>{validation.status_counts["제외"] ?? 0}</strong>
            </article>
            <article className="danger">
              <span>처리 불가</span>
              <strong>{validation.status_counts["오류"] ?? 0}</strong>
            </article>
            <article className="warning">
              <span>확인 필요</span>
              <strong>{validation.status_counts["검토"] ?? 0}</strong>
            </article>
          </div>
          {validation.rows.some((row) => ["오류", "검토"].includes(row.status)) && (
            <section className="panel row-review-panel">
              <div className="panel-heading">
                <div>
                  <h2>확인이 필요한 원본 행</h2>
                  <p>내용과 사유를 확인하고 업무 데이터가 아닌 행만 직접 제외하세요.</p>
                </div>
              </div>
              <div className="row-review-list">
                {validation.rows
                  .filter((row) => ["오류", "검토"].includes(row.status))
                  .map((row) => (
                    <article key={row.id}>
                      <div className="row-review-heading">
                        <div>
                          <strong>원본 {row.row_number}행 · {row.status === "오류" ? "처리 불가" : "확인 필요"}</strong>
                          <span>{row.issues.join(" · ")}</span>
                        </div>
                        <button
                          type="button"
                          className="danger-button"
                          disabled={Boolean(busy)}
                          onClick={() => void excludeRow(row)}
                        >
                          {busy === `exclude-row-${row.id}` ? "제외 중…" : "이 행 제외"}
                        </button>
                      </div>
                      <div className="raw-row-values">
                        {row.raw_values.map((value, index) => (
                          <div key={index}>
                            <span>{validation.headers[index] || `${index + 1}열`}</span>
                            <b>{value === null || value === undefined || value === "" ? "(빈값)" : String(value)}</b>
                          </div>
                        ))}
                      </div>
                    </article>
                  ))}
              </div>
            </section>
          )}
          <section className="panel">
            <div className="panel-heading">
              <div>
                <h2>수량·금액 비교</h2>
                <p>원본 합계와 Sales Master에 들어갈 합계를 비교합니다.</p>
              </div>
              <span className="pill blue">
                <i />
                {validation.application_mode}
              </span>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>통화</th>
                    <th className="numeric">원본 수량</th>
                    <th className="numeric">반영 수량</th>
                    <th className="numeric">원본 Net</th>
                    <th className="numeric">반영 Net</th>
                    <th className="numeric">원본 Gross</th>
                    <th className="numeric">반영 Gross</th>
                    <th>계산 기준</th>
                  </tr>
                </thead>
                <tbody>
                  {validation.reconciliation.map((item) => (
                    <tr key={item.currency}>
                      <td>
                        <strong>{item.currency}</strong>
                      </td>
                      <td className="numeric">
                        {number.format(item.original_quantity)}
                      </td>
                      <td className="numeric">
                        {number.format(item.planned_quantity)}
                      </td>
                      <td className="numeric">
                        {item.net_available
                          ? number.format(item.original_net)
                          : "금액 없음"}
                      </td>
                      <td className="numeric">
                        {item.net_available
                          ? number.format(item.planned_net)
                          : "—"}
                      </td>
                      <td className="numeric">
                        {item.gross_available
                          ? number.format(item.original_gross)
                          : "금액 없음"}
                      </td>
                      <td className="numeric">
                        {item.gross_available
                          ? number.format(item.planned_gross)
                          : "—"}
                      </td>
                      <td>
                        <span className="cell-secondary">
                          {item.net_basis}
                          <br />
                          {item.gross_basis}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <section className="panel validation-table">
            <div className="panel-heading">
              <div>
                <h2>행별 처리 결과</h2>
                <p>{validation.row_count}개 데이터 행의 반영 상태입니다.</p>
              </div>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>원본 행</th>
                    <th>상태</th>
                    <th>PO / 라인</th>
                    <th>모델 / 고객</th>
                    <th className="numeric">수량</th>
                    <th>통화</th>
                    <th>생산정보 연결 / 지역</th>
                    <th>처리 내용</th>
                  </tr>
                </thead>
                <tbody>
                  {validation.rows.map((row) => (
                    <tr key={row.id}>
                      <td>{row.row_number}행</td>
                      <td>
                        <span
                          className={`pill ${row.status === "처리" ? "green" : row.status === "오류" ? "red" : row.status === "검토" ? "amber" : "gray"}`}
                        >
                          <i />
                          {row.status === "처리"
                            ? "사용"
                            : row.status === "오류"
                              ? "처리 불가"
                              : row.status === "검토"
                                ? "확인 필요"
                                : "제외"}
                        </span>
                      </td>
                      <td>
                        <strong>{row.po_number ?? "미확인"}</strong>
                        <span className="cell-secondary">
                          {row.po_line_number ?? "라인 없음"}
                        </span>
                      </td>
                      <td>
                        <strong>{row.model ?? "미확인"}</strong>
                        <span className="cell-secondary">
                          {row.customer ?? "미확인"}
                        </span>
                      </td>
                      <td className="numeric">{row.quantity ?? "—"}</td>
                      <td>{row.currency ?? "—"}</td>
                      <td>
                        <strong>반영 후 자동 매칭</strong>
                        <span className="cell-secondary">
                          {row.sales_region ?? "미확인"}
                        </span>
                      </td>
                      <td className="issues-cell">
                        {row.issues.length ? (
                          row.issues.map((issue) => (
                            <span key={issue}>• {issue}</span>
                          ))
                        ) : (
                          <span className="ok-text">반영 가능</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <section
            className={`apply-panel ${validation.can_apply ? "ready" : "blocked"}`}
          >
            <div>
              <strong>
                {validation.can_apply
                  ? "Sales Master 반영 준비가 끝났습니다."
                  : "확인이 필요한 행이 있습니다."}
              </strong>
              <p>
                {validation.can_apply
                  ? validation.application_mode === "최초 반영"
                    ? "반영 버튼을 누르면 Sales Master에 저장합니다."
                    : "기존 주문과 Closed 기록은 유지하고 새 버전으로 보관합니다."
                  : "위 원본 행을 확인하고 업무 데이터가 아닌 행은 제외하세요. 실제 주문행이라면 원본 또는 매핑을 수정해야 합니다."}
              </p>
            </div>
            <button
              className="primary-button"
              type="button"
              disabled={!validation.can_apply || Boolean(busy)}
              onClick={() => void apply()}
            >
              {busy === "apply" ? "처리 중…" : validation.apply_label}
            </button>
            {validation.batch.status === "반영 완료" && (
              <Link className="outline-button" href="/sales-master">
                Sales Master 확인 →
              </Link>
            )}
          </section>
        </section>
      )}
    </>
  );
}
