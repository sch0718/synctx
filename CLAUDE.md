@AGENTS.md

## 웹 화면 (Claude 전용)

화면 작업 규칙은 `~/.claude/rules/modern-web.md`(웹 파일을 다룰 때 자동으로 실림)를 따르고, 이 저장소에서는 아래를 더한다.

- 대상: Viewer 정적 파일(`services/viewer/src/static/`)과 Viewer가 내보내는 HTML·CSS. Python 서비스, Helm, 스크립트는 대상이 아니다.
- 배포 전 검사: `scripts/legacy-scan.sh`가 버전을 고정한 legacy-scan으로 Viewer 정적 파일을 검사하고, 허용 목록(`scripts/legacy-scan-allow.txt`) 밖의 후보가 있으면 실패한다. 정적 내보내기 워크플로가 빌드 전에 돌리고, 릴리스 전에는 직접 돌린다. 새 후보는 확인해 고치거나, 일부러 둔 코드면 이유와 함께 허용 목록에 올린다.
- 폐쇄망: 이 도구는 외부(GitHub) 개발 환경에서만 쓴다. 이미지·vendoring·런타임과 폐쇄망 배포 스크립트(Helm·Docker)에 넣지 않는다. 폐쇄망 사내 브라우저 버전을 확인하지 못했으므로 Baseline Newly available 기능에는 대체 코드를 둔다.
