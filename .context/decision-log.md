# 의사결정 로그 (Decision Log)

## 2026-06-19: 프론트엔드 스택 결정
- **결정**: Viewer Service의 기본 렌더링은 Vanilla JS + HTML로 유지하되, 향후 복잡한 상호작용(스크럼 보드 등)이 필요한 경우 사내 `example-fe`의 React/Vite 스택을 선택적으로 도입한다.
- **사유**: 불필요한 Vue/Alpine.js 의존성을 제거하고 사내 표준 스택과 일관성을 맞추기 위함.
