{% load static %}// Service worker da Central Zanattex.
//
// O app é essencialmente dados ao vivo (produção, carteira, metas) — por
// isso NÃO cacheamos páginas de dashboard nem tentamos um "modo offline"
// completo, o que faria alguém tomar decisão em cima de número velho sem
// saber. O que fazemos:
//   1. Deixa o app instalável (critério do Chrome: manifest + SW + fetch).
//   2. Cacheia o "esqueleto" estático (CSS, ícones) pra carregar mais rápido
//      nas visitas seguintes.
//   3. Se a rede cair no meio de uma navegação, mostra uma tela de "sem
//      conexão" em vez de erro cru do navegador.
const VERSAO = "v1";
const CACHE_SHELL = "zanattex-shell-" + VERSAO;

const SHELL = [
    "{% static 'css/app.css' %}",
    "{% static 'favicon.svg' %}",
    "{% static 'icons/icon-192.png' %}",
    "{% static 'icons/icon-512.png' %}",
    "{% url 'offline' %}",
];

self.addEventListener("install", (event) => {
    event.waitUntil(
        caches.open(CACHE_SHELL)
            .then((cache) => cache.addAll(SHELL))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys()
            .then((chaves) => Promise.all(
                chaves.filter((k) => k !== CACHE_SHELL).map((k) => caches.delete(k))
            ))
            .then(() => self.clients.claim())
    );
});

self.addEventListener("fetch", (event) => {
    const req = event.request;
    if (req.method !== "GET" || new URL(req.url).origin !== self.location.origin) return;

    // Navegação (usuário abrindo/trocando de página): sempre busca da rede
    // primeiro — dado de dashboard não pode vir de cache. Só cai pro
    // fallback offline se a rede realmente falhar (sem internet).
    if (req.mode === "navigate") {
        event.respondWith(
            fetch(req).catch(() => caches.match("{% url 'offline' %}"))
        );
        return;
    }

    // Estático (CSS/ícones): cache primeiro pra ser instantâneo, atualiza
    // em segundo plano pra próxima visita já vir com a versão nova.
    if (req.url.includes("/static/")) {
        event.respondWith(
            caches.match(req).then((emCache) => {
                const rede = fetch(req).then((resp) => {
                    caches.open(CACHE_SHELL).then((cache) => cache.put(req, resp.clone()));
                    return resp;
                }).catch(() => emCache);
                return emCache || rede;
            })
        );
    }
});
