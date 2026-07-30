"""Report profiles shared by note composition and deterministic rendering."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReportPreset(StrEnum):
    """Audience and detail presets exposed by the desktop application."""

    CONCISE = "concise"
    DETAILED = "detailed"
    PROFESSIONAL = "professional"
    BEGINNER = "beginner"
    EXECUTIVE = "executive"


class OutputFormat(StrEnum):
    """Public note artifacts.

    Markdown is always required because it is the canonical portable output.
    HTML is also used as the deterministic source when PDF is requested.
    """

    MARKDOWN = "markdown"
    HTML = "html"
    PDF = "pdf"


class ReportSpec(BaseModel):
    """User-facing report request with optional per-run overrides."""

    model_config = ConfigDict(extra="forbid")

    preset: ReportPreset = ReportPreset.DETAILED
    language: str = Field(default="zh-CN", min_length=2, max_length=32)
    max_sections: int | None = Field(default=None, ge=1, le=80)
    max_takeaways: int | None = Field(default=None, ge=1, le=20)
    include_glossary: bool | None = None
    include_evidence_index: bool = True
    include_screenshots: bool = True
    output_formats: set[OutputFormat] = Field(
        default_factory=lambda: {
            OutputFormat.MARKDOWN,
            OutputFormat.HTML,
            OutputFormat.PDF,
        }
    )
    template_version: str = Field(default="1", min_length=1, max_length=32)

    @model_validator(mode="after")
    def require_markdown(self) -> Self:
        if OutputFormat.MARKDOWN not in self.output_formats:
            raise ValueError("Markdown is the canonical output and cannot be disabled")
        return self

    def resolve(self) -> ResolvedReportSpec:
        policy = _PRESET_POLICIES[self.preset]
        return ResolvedReportSpec(
            preset=self.preset,
            language=self.language,
            audience=policy.audience,
            editorial_goal=policy.editorial_goal,
            max_sections=self.max_sections or policy.max_sections,
            max_facts_per_section=policy.max_facts_per_section,
            max_takeaways=self.max_takeaways or policy.max_takeaways,
            include_glossary=(
                policy.include_glossary
                if self.include_glossary is None
                else self.include_glossary
            ),
            include_evidence_index=self.include_evidence_index,
            include_screenshots=self.include_screenshots,
            output_formats=set(self.output_formats),
            template_version=self.template_version,
            max_output_tokens=policy.max_output_tokens,
        )


class ResolvedReportSpec(BaseModel):
    """Concrete composition policy persisted with the generated note."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    preset: ReportPreset
    language: str
    audience: str
    editorial_goal: str
    max_sections: int = Field(ge=1)
    max_facts_per_section: int = Field(ge=1)
    max_takeaways: int = Field(ge=1)
    include_glossary: bool
    include_evidence_index: bool
    include_screenshots: bool
    output_formats: set[OutputFormat]
    template_version: str
    max_output_tokens: int = Field(ge=1)


class _PresetPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    audience: str
    editorial_goal: str
    max_sections: int
    max_facts_per_section: int
    max_takeaways: int
    include_glossary: bool
    max_output_tokens: int


_PRESET_POLICIES: dict[ReportPreset, _PresetPolicy] = {
    ReportPreset.CONCISE: _PresetPolicy(
        audience="希望快速掌握结论的普通读者",
        editorial_goal="只保留核心结论、必要依据和最短的上下文，合并重复细节。",
        max_sections=6,
        max_facts_per_section=6,
        max_takeaways=4,
        include_glossary=False,
        max_output_tokens=8_192,
    ),
    ReportPreset.DETAILED: _PresetPolicy(
        audience="需要完整复盘视频内容的读者",
        editorial_goal="尽可能覆盖有效内容、时间顺序、关键术语、限制与可回看依据。",
        max_sections=24,
        max_facts_per_section=16,
        max_takeaways=8,
        include_glossary=True,
        max_output_tokens=16_384,
    ),
    ReportPreset.PROFESSIONAL: _PresetPolicy(
        audience="熟悉主题、关注准确术语与方法边界的专业读者",
        editorial_goal="突出方法、数据、假设、限制、不确定性和可复核证据。",
        max_sections=18,
        max_facts_per_section=12,
        max_takeaways=6,
        include_glossary=True,
        max_output_tokens=14_336,
    ),
    ReportPreset.BEGINNER: _PresetPolicy(
        audience="第一次接触主题的入门读者",
        editorial_goal="使用短句和清晰步骤解释术语，但不得用外部常识补造背景。",
        max_sections=12,
        max_facts_per_section=10,
        max_takeaways=6,
        include_glossary=True,
        max_output_tokens=12_288,
    ),
    ReportPreset.EXECUTIVE: _PresetPolicy(
        audience="只阅读结论、影响、风险与决策信息的管理者",
        editorial_goal=(
            "优先呈现结论、影响、风险、决策和行动项；证据没有行动项时必须明确说明，"
            "不得编造。"
        ),
        max_sections=8,
        max_facts_per_section=8,
        max_takeaways=5,
        include_glossary=False,
        max_output_tokens=9_216,
    ),
}
