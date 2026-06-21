#!/bin/sh
# Slides 디렉토리의 .html 파일을 스캔하여 인덱스 페이지를 생성한다.
# sync-sidecar.sh에서 git pull 후 호출된다.

SLIDES_DIR="${1:-/data/slides/worktree/_published/slides}"
INDEX_FILE="$SLIDES_DIR/index.html"

generate_index() {
    if [ ! -d "$SLIDES_DIR" ]; then
        echo "Slides directory not found: $SLIDES_DIR"
        return 1
    fi

    file_rows=""
    file_count=0

    for f in "$SLIDES_DIR"/*.html; do
        [ -f "$f" ] || continue
        fname=$(basename "$f")
        [ "$fname" = "index.html" ] && continue

        file_count=$((file_count + 1))
        fsize=$(wc -c < "$f" | tr -d ' ')
        fdate=$(date -r "$f" -u +%Y-%m-%d 2>/dev/null || stat -c %y "$f" 2>/dev/null | cut -d' ' -f1)
        title=$(echo "$fname" | sed 's/\.html$//')

        if [ "$fsize" -ge 1048576 ]; then
            size_display="$(echo "$fsize" | awk '{printf "%.1f MB", $1/1048576}')"
        elif [ "$fsize" -ge 1024 ]; then
            size_display="$(echo "$fsize" | awk '{printf "%.1f KB", $1/1024}')"
        else
            size_display="${fsize} B"
        fi

        file_rows="$file_rows"'
        <a href="'"$fname"'" class="slide-card">
            <div class="slide-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                    <rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8"/><path d="M12 17v4"/>
                    <path d="m8 10 3 2-3 2"/><line x1="13" y1="14" x2="16" y2="14"/>
                </svg>
            </div>
            <div class="slide-info">
                <div class="slide-title">'"$title"'</div>
                <div class="slide-meta">'"$size_display"' &middot; '"$fdate"'</div>
            </div>
        </a>'
    done

    if [ "$file_count" -eq 0 ]; then
        main_content='
        <div class="empty-state">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8"/><path d="M12 17v4"/>
            </svg>
            <p>아직 발행된 프레젠테이션이 없습니다.</p>
            <p class="empty-hint">Viewer에서 Marp 마크다운 파일을 열고 "출판" 버튼을 눌러 슬라이드를 발행하세요.</p>
        </div>'
    else
        main_content='
        <div class="content-header">
            <div class="section-title">발행된 프레젠테이션</div>
            <span class="file-count">'"$file_count"'개 파일</span>
        </div>
        <div class="slide-grid">'"$file_rows"'
        </div>'
    fi

    cat > "$INDEX_FILE" << 'HTMLHEAD'
<!DOCTYPE html>
<html lang="ko" data-theme="github-light">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>synctx Slides</title>
    <script>
        (function() {
            var saved = localStorage.getItem('synctx-theme');
            if (saved) document.documentElement.setAttribute('data-theme', saved);
        })();
    </script>
    <style>
[data-theme="github-light"] {
    --primary: #0969da; --primary-light: #4493f8; --primary-lighter: #ddf4ff; --primary-dark: #0550ae;
    --bg-default: #ffffff; --bg-neutral: #f6f8fa; --bg-card: #ffffff;
    --text-primary: #1f2328; --text-secondary: #57606a; --text-disabled: #8b949e;
    --border-color: rgba(208,215,222,0.5);
    --shadow-card: 0 0 2px 0 rgba(145,158,171,0.2), 0 12px 24px -4px rgba(145,158,171,0.12);
    --shadow-sm: 0 1px 2px 0 rgba(145,158,171,0.16);
}
[data-theme="github-dark"] {
    --primary: #58a6ff; --primary-light: #79c0ff; --primary-lighter: #0d1117; --primary-dark: #388bfd;
    --bg-default: #0d1117; --bg-neutral: #161b22; --bg-card: #161b22;
    --text-primary: #e6edf3; --text-secondary: #8b949e; --text-disabled: #484f58;
    --border-color: rgba(48,54,61,0.6);
    --shadow-card: 0 0 2px 0 rgba(0,0,0,0.4), 0 12px 24px -4px rgba(0,0,0,0.3);
    --shadow-sm: 0 1px 2px 0 rgba(0,0,0,0.3);
}
[data-theme="cayman"] {
    --primary: #159957; --primary-light: #1e9b52; --primary-lighter: #e6f9ef; --primary-dark: #0e7a42;
    --bg-default: #ffffff; --bg-neutral: #f3f6fa; --bg-card: #ffffff;
    --text-primary: #606c71; --text-secondary: #819198; --text-disabled: #9aa6ac;
    --border-color: rgba(220,230,240,0.5);
    --shadow-card: 0 0 2px 0 rgba(145,158,171,0.2), 0 12px 24px -4px rgba(145,158,171,0.12);
    --shadow-sm: 0 1px 2px 0 rgba(145,158,171,0.16);
}
[data-theme="minimal"] {
    --primary: #4183c4; --primary-light: #6ba4d9; --primary-lighter: #e8f0fa; --primary-dark: #2e6da4;
    --bg-default: #ffffff; --bg-neutral: #f8f8f8; --bg-card: #ffffff;
    --text-primary: #333333; --text-secondary: #777777; --text-disabled: #999999;
    --border-color: rgba(222,222,222,0.5);
    --shadow-card: 0 0 2px 0 rgba(145,158,171,0.2), 0 12px 24px -4px rgba(145,158,171,0.12);
    --shadow-sm: 0 1px 2px 0 rgba(145,158,171,0.16);
}
[data-theme="hacker"] {
    --primary: #b5e853; --primary-light: #c4ee7e; --primary-lighter: #1a2a1a; --primary-dark: #8fc43e;
    --bg-default: #151515; --bg-neutral: #1a1a1a; --bg-card: #1a1a1a;
    --text-primary: #b0b0b0; --text-secondary: #888888; --text-disabled: #666666;
    --border-color: rgba(51,51,51,0.6);
    --shadow-card: 0 0 2px 0 rgba(0,0,0,0.4), 0 12px 24px -4px rgba(0,0,0,0.3);
    --shadow-sm: 0 1px 2px 0 rgba(0,0,0,0.3);
}
[data-theme="modernist"] {
    --primary: #4276b6; --primary-light: #6694c8; --primary-lighter: #e8f0fa; --primary-dark: #325fa0;
    --bg-default: #f1f1f1; --bg-neutral: #e8f0fa; --bg-card: #ffffff;
    --text-primary: #474747; --text-secondary: #7a8a96; --text-disabled: #9aa6ac;
    --border-color: rgba(197,209,219,0.5);
    --shadow-card: 0 0 2px 0 rgba(145,158,171,0.2), 0 12px 24px -4px rgba(145,158,171,0.12);
    --shadow-sm: 0 1px 2px 0 rgba(145,158,171,0.16);
}
[data-theme="corporate"] {
    --primary: #E01E1F; --primary-light: #E84A4B; --primary-lighter: #FDE8E8; --primary-dark: #B01819;
    --bg-default: #ffffff; --bg-neutral: #F4F6F8; --bg-card: #ffffff;
    --text-primary: #1C252E; --text-secondary: #637381; --text-disabled: #919EAB;
    --border-color: rgba(145,158,171,0.2);
    --shadow-card: 0 0 2px 0 rgba(145,158,171,0.2), 0 12px 24px -4px rgba(145,158,171,0.12);
    --shadow-sm: 0 1px 2px 0 rgba(145,158,171,0.16);
}
    </style>
    <style>
:root {
    --radius-sm: 8px;
    --radius-md: 12px;
    --radius-lg: 16px;
    --font-primary: 'Public Sans', -apple-system, BlinkMacSystemFont, sans-serif;
    --font-mono: 'D2Coding', ui-monospace, monospace;
    --header-height: 64px;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: var(--font-primary); background: var(--bg-default); color: var(--text-primary); }

.header {
    height: var(--header-height); display: flex; align-items: center;
    padding: 0 24px; background: var(--bg-card); border-bottom: 1px solid var(--border-color);
    position: fixed; top: 0; left: 0; right: 0; z-index: 100;
}
.header-logo { font-size: 18px; font-weight: 700; color: var(--primary); display: flex; align-items: center; gap: 8px; text-decoration: none; }
.header-logo svg { width: 28px; height: 28px; }
.header-logo span { color: var(--text-secondary); font-weight: 400; font-size: 14px; }
.header-actions { margin-left: auto; display: flex; gap: 8px; align-items: center; }
.theme-select {
    padding: 6px 12px; border: 1px solid var(--border-color); border-radius: var(--radius-sm);
    background: var(--bg-card); color: var(--text-primary); font-size: 13px;
    font-family: var(--font-primary); cursor: pointer;
}
.theme-select:focus { outline: 2px solid var(--primary); outline-offset: -2px; }

.page-body {
    margin-top: var(--header-height); padding: 32px;
    max-width: 960px; margin-left: auto; margin-right: auto;
    min-height: calc(100vh - var(--header-height));
}

.content-header {
    display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px;
}
.section-title { font-size: 18px; font-weight: 700; }
.file-count { font-size: 13px; color: var(--text-disabled); font-weight: 500; }

.slide-grid {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px;
}
.slide-card {
    display: flex; align-items: center; gap: 16px;
    background: var(--bg-card); border-radius: var(--radius-lg); box-shadow: var(--shadow-card);
    padding: 20px; cursor: pointer; transition: transform .15s, box-shadow .15s;
    text-decoration: none; color: var(--text-primary);
}
.slide-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 0 2px 0 rgba(145,158,171,0.2), 0 20px 40px -4px rgba(145,158,171,0.16);
}
[data-theme="github-dark"] .slide-card:hover,
[data-theme="hacker"] .slide-card:hover {
    box-shadow: 0 0 2px 0 rgba(0,0,0,0.5), 0 20px 40px -4px rgba(0,0,0,0.4);
}
.slide-icon {
    flex-shrink: 0; width: 48px; height: 48px; border-radius: var(--radius-md);
    background: var(--primary-lighter); display: flex; align-items: center; justify-content: center;
}
.slide-icon svg { width: 24px; height: 24px; color: var(--primary); }
.slide-info { flex: 1; min-width: 0; }
.slide-title { font-size: 15px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.slide-meta { font-size: 12px; color: var(--text-disabled); margin-top: 4px; }

.empty-state { text-align: center; padding: 80px 20px; color: var(--text-secondary); }
.empty-state svg { width: 64px; height: 64px; color: var(--text-disabled); margin-bottom: 16px; }
.empty-state p { font-size: 15px; margin-bottom: 8px; }
.empty-hint { font-size: 13px; color: var(--text-disabled); }

@media (max-width: 768px) {
    .page-body { padding: 16px; }
    .slide-grid { grid-template-columns: 1fr; }
}
    </style>
</head>
<body>
<header class="header">
    <a href="/slides/" class="header-logo">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 2v4m0 12v4M2 12h4m12 0h4m-3.5-6.5L17 7m-10 10-1.5 1.5M20.5 17.5 19 17M5 7 3.5 5.5"/></svg>
        synctx <span>Slides</span>
    </a>
    <div class="header-actions">
        <select id="theme-select" class="theme-select" onchange="changeTheme(this.value)">
            <option value="github-light">GitHub Light</option>
            <option value="github-dark">GitHub Dark</option>
            <option value="cayman">Cayman</option>
            <option value="minimal">Minimal</option>
            <option value="hacker">Hacker</option>
            <option value="modernist">Modernist</option>
            <option value="corporate">Corporate</option>
        </select>
    </div>
</header>

<div class="page-body">
HTMLHEAD

    echo "$main_content" >> "$INDEX_FILE"

    cat >> "$INDEX_FILE" << 'HTMLTAIL'
</div>
<script>
(function() {
    var saved = localStorage.getItem('synctx-theme');
    if (saved) {
        var sel = document.getElementById('theme-select');
        if (sel) sel.value = saved;
    }
})();
function changeTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('synctx-theme', theme);
}
</script>
</body>
</html>
HTMLTAIL

    echo "Index generated: $file_count presentation(s)"
}

generate_index
