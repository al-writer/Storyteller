// Проект на Railway: один сервис из Dockerfile и том для SQLite и кэша открыток.
// Применение — `railway config apply`, подробности в README, раздел «Деплой на Railway».
import { defineRailway, github, project, service, volume } from "railway/iac";

export default defineRailway(() => {
  const data = volume("storyteller-data");

  const app = service("storyteller", {
    source: github("al-writer/Storyteller", { branch: "main" }),
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    healthcheck: "/healthz",
    // Больше одной реплики нельзя: SQLite и лимиты запросов живут в одном процессе,
    // а Railway не подключает том к нескольким репликам.
    replicas: 1,
    volumeMounts: {
      "/data": data,
    },
  });

  return project("storyteller", {
    resources: [app, data],
  });
});
