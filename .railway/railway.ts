// Проект на Railway: один сервис из Dockerfile и том для SQLite и кэша открыток.
// Применение — `railway config apply`, подробности в README, раздел «Деплой на Railway».
import { defineRailway, github, project, service, volume } from "railway/iac";

export default defineRailway(() => {
  const data = volume("storyteller-data");

  const app = service("storyteller", {
    // checkSuites: деплой только после зелёных проверок GitHub (CI) на коммите.
    source: github("al-writer/Storyteller", { branch: "main", checkSuites: true }),
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    healthcheck: "/healthz",
    // Больше одной реплики нельзя: SQLite и лимиты запросов живут в одном процессе,
    // а Railway не подключает том к нескольким репликам.
    replicas: 1,
    volumeMounts: {
      "/data": data,
    },
    // Публичный адрес: как и остальные ресурсы проекта, домен описан в этом файле.
    networking: {
      serviceDomains: {
        "plotslot.up.railway.app": {},
      },
    },
  });

  return project("storyteller", {
    resources: [app, data],
  });
});
