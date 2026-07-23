export type AppBuildInfo = {
  version: string;
  revision: string;
  builtAt: string;
};

declare const __APP_BUILD_INFO__: AppBuildInfo;

export const APP_BUILD_INFO = Object.freeze(__APP_BUILD_INFO__);

export function buildInfoLabel(info: AppBuildInfo = APP_BUILD_INFO): string {
  const compactTime = info.builtAt.replace(/[-:]/g, "").replace(".000Z", "Z");
  return `v${info.version} · ${info.revision} · ${compactTime}`;
}

export function buildInfoTitle(info: AppBuildInfo = APP_BUILD_INFO): string {
  return `构建版本 ${info.version}；修订 ${info.revision}；构建时间 ${info.builtAt}`;
}
