// Проект на Railway: один сервис из Dockerfile и том для SQLite и кэша открыток.
// Применение — `railway config apply`, подробности в README, раздел «Деплой на Railway».
import { defineRailway, github, preserve, project, service, volume } from "railway/iac";

export default defineRailway(() => {
  // Регион и размер — как у созданного тома: без них `config apply` сбросит их в null.
  const data = volume("storyteller-data", { region: "europe-west4-drams3a", sizeMB: 5000 });

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
    variables: {
      // Токен бота — секрет: задаётся в панели Railway, файл только сохраняет его значение.
      TELEGRAM_BOT_TOKEN: preserve(),
      // Telegram ID через запятую: им доступна команда /stats.
      TELEGRAM_ADMIN_IDS: "438452289",
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
