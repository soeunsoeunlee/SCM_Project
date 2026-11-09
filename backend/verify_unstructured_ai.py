"""줄글과 이미지가 OpenAI 구조화 추출 입력으로 전달되는지 네트워크 없이 검증합니다."""
from app import ai_mapping


def main() -> None:
    captured: list[list[dict]] = []
    original_configured = ai_mapping.is_openai_configured
    original_request = ai_mapping._structured_content_response
    ai_mapping.is_openai_configured = lambda: True

    def fake_request(content, schema, schema_name, model_env):
        captured.append(content)
        return {
            "status": "used",
            "message": "모의 추출",
            "data": {"document_summary": "검증", "records": [], "warnings": []},
        }

    ai_mapping._structured_content_response = fake_request
    try:
        text_result = ai_mapping.extract_unstructured_with_ai("판매계획", text="PO 0001 모델 A 수량 10")
        image_result = ai_mapping.extract_unstructured_with_ai(
            "선적정보", image_bytes=b"fake-image", image_media_type="image/png"
        )
    finally:
        ai_mapping.is_openai_configured = original_configured
        ai_mapping._structured_content_response = original_request

    assert text_result["status"] == "used" and image_result["status"] == "used"
    assert any(item["type"] == "input_text" and "PO 0001" in item["text"] for item in captured[0])
    image_items = [item for item in captured[1] if item["type"] == "input_image"]
    assert len(image_items) == 1 and image_items[0]["image_url"].startswith("data:image/png;base64,")
    print("통과: 줄글은 input_text, 이미지는 input_image로 OpenAI 구조화 추출에 전달")


if __name__ == "__main__":
    main()
