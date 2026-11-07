import { handleAdminApi } from '../../../src/admin/api';
import type { Env } from '../../../src/env';

export const onRequest: PagesFunction<Env> = (ctx) => handleAdminApi(ctx.request, ctx.env);
