import type { Env } from '../src/env';
import { handleRequest } from '../src/middleware';

export const onRequest: PagesFunction<Env> = (ctx) =>
  handleRequest({ request: ctx.request, env: ctx.env, next: () => ctx.next() });
