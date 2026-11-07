/** Pages 바인딩·환경변수(docs/install.md 「로그인 모드」). 시크릿 값은 저장소에 두지 않는다. */
export interface Env {
  DB: D1Database;
  ASSETS: Fetcher;
  /** 전체 관리자 identity, 콤마 구분(시크릿) */
  OWNER_ACCOUNTS?: string;
  FIREBASE_PROJECT_ID?: string;
  /** Firebase 웹 앱 설정 JSON(apiKey·authDomain·projectId·appId). 비밀이 아니다(일반 환경변수) */
  FIREBASE_WEB_CONFIG?: string;
  KAKAO_CLIENT_ID?: string;
  KAKAO_CLIENT_SECRET?: string;
  NAVER_CLIENT_ID?: string;
  NAVER_CLIENT_SECRET?: string;
}
