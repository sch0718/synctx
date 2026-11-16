# 서드파티 라이브러리 고지

Viewer에 번들된 파일과 `scripts/download-cdn-vendor.sh`가 CDN 이미지용으로 받는 라이브러리의 고지다. 아래 라이브러리에는 각각의 라이선스가 적용된다. 라이선스 원문은 영어로 보존한다. 번들 파일 안에 있는 추가 의존성의 저작권·라이선스 주석도 그대로 유지한다.

## highlight.js 11.9.0

- 라이선스: BSD-3-Clause
- 프로젝트: https://github.com/highlightjs/highlight.js
- 원본 라이선스: https://github.com/highlightjs/highlight.js/blob/11.9.0/LICENSE
- 배포 출처: https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11.9.0/
- Viewer 파일: `services/viewer/src/static/vendor/highlight.min.js`, `services/viewer/src/static/vendor/hljs/{github,github-dark,atom-one-light,monokai}.min.css`
- CDN 파일: `cdn-data/highlight.js/11.9.0/highlight.min.js`, `cdn-data/highlight.js/11.9.0/styles/{github,github-dark,atom-one-light,monokai}.min.css`

버전과 라이선스 이름은 JS 파일 머리말에서 확인했다. 라이선스 원문과 저작권 줄은 upstream 11.9.0 태그의 LICENSE와 같다(2026-11-16 확인). 11.9.0 배포 파일에는 아래처럼 저작권자 이름이 `undefined`로 표시되어 있어 그 머리말도 함께 보존한다.

```text
Highlight.js v11.9.0 (git: f47103d4f1)
(c) 2006-2023 undefined and other contributors
License: BSD-3-Clause
```

GitHub·GitHub Dark 테마 CSS의 원래 주석에는 `Author: github.com`, `Maintainer: @Hirse`, `Updated: 2021-05-15`가 적혀 있다. Atom One Light·Monokai CSS에는 별도 저작권 줄이 없어 저작권자나 연도를 추가로 추정하지 않았다.

```text
BSD 3-Clause License

Copyright (c) 2006, Ivan Sagalaev.
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the copyright holder nor the names of its
  contributors may be used to endorse or promote products derived from
  this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

## Mermaid 10.9.1 (다운로드 스크립트 기준)

- 라이선스: MIT
- 프로젝트: https://github.com/mermaid-js/mermaid
- 원본 라이선스: https://github.com/mermaid-js/mermaid/blob/v10.9.1/LICENSE
- 다운로드 출처: https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js
- Viewer 파일: `services/viewer/src/static/vendor/mermaid.min.js`
- CDN 파일: `cdn-data/mermaid/10.9.1/mermaid.min.js`

Viewer 파일에는 Mermaid 자체의 버전 고지가 없어 `scripts/download-cdn-vendor.sh`의 `MERMAID_VER="10.9.1"`을 적었다. Viewer 번들과 CDN 파일의 버전이 같다는 것을 파일에서 확인한 것은 아니다. 라이선스 원문과 저작권 줄은 upstream v10.9.1 태그의 LICENSE와 같다(2026-11-16 확인).

```text
The MIT License (MIT)

Copyright (c) 2014 - 2022 Knut Sveidqvist

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

## Chart.js 4.4.4

- 라이선스: MIT
- 프로젝트: https://github.com/chartjs/Chart.js
- 원본 라이선스: https://github.com/chartjs/Chart.js/blob/v4.4.4/LICENSE.md
- 다운로드 출처: https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js
- CDN 파일: `cdn-data/chart.js/4.4.4/chart.umd.min.js`

버전과 라이선스 이름은 CDN 파일 머리말에서, 라이선스 원문과 저작권 줄은 upstream v4.4.4 태그의 LICENSE.md에서 확인했다(2026-11-16).

```text
The MIT License (MIT)

Copyright (c) 2014-2024 Chart.js Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```
