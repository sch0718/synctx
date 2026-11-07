import type { Env } from '../env';

export interface RepoInfo {
  id: string;
  name: string;
}

/** 빌드가 남긴 site/_synctx/repos.json(id·이름). middleware가 브라우저 요청을 막고 여기서만 env.ASSETS로 읽는다. */
export async function loadRepos(env: Env, req: Request): Promise<RepoInfo[]> {
  const res = await env.ASSETS.fetch(new URL('/_synctx/repos.json', req.url));
  if (!res.ok) throw new Error(`repos.json HTTP ${res.status}`);
  const data: unknown = await res.json();
  if (!Array.isArray(data)) throw new Error('repos.json is not a list');
  return data.map((r) => {
    if (typeof r?.id !== 'string' || typeof r?.name !== 'string') throw new Error('repos.json row');
    return { id: r.id, name: r.name };
  });
}
