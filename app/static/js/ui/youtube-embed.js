(() => {
  function loadYouTubeIframeApi() {
    if (window.YT && typeof window.YT.Player === "function") {
      return Promise.resolve(window.YT);
    }
    if (window.__ytIframeApiPromise) {
      return window.__ytIframeApiPromise;
    }

    window.__ytIframeApiPromise = new Promise((resolve, reject) => {
      let settled = false;
      let timeoutId = null;

      const resolveOnce = (value) => {
        if (settled) return;
        settled = true;
        if (timeoutId !== null) {
          window.clearTimeout(timeoutId);
        }
        resolve(value);
      };
      const rejectOnce = (error) => {
        if (settled) return;
        settled = true;
        if (timeoutId !== null) {
          window.clearTimeout(timeoutId);
        }
        reject(error);
      };

      const ready = () => {
        if (window.YT && typeof window.YT.Player === "function") {
          resolveOnce(window.YT);
        } else {
          rejectOnce(new Error("youtube_iframe_api_not_ready"));
        }
      };

      const previousReady = window.onYouTubeIframeAPIReady;
      window.onYouTubeIframeAPIReady = () => {
        if (typeof previousReady === "function") {
          previousReady();
        }
        ready();
      };

      timeoutId = window.setTimeout(() => {
        rejectOnce(new Error("youtube_iframe_api_timeout"));
      }, 12000);

      const existingScript = document.querySelector("script[data-youtube-iframe-api='1']");
      if (!existingScript) {
        const script = document.createElement("script");
        script.src = "https://www.youtube.com/iframe_api";
        script.async = true;
        script.dataset.youtubeIframeApi = "1";
        script.onerror = () => rejectOnce(new Error("youtube_iframe_api_load_failed"));
        document.head.appendChild(script);
        return;
      }

      if (window.YT && typeof window.YT.Player === "function") {
        ready();
      }
    });

    return window.__ytIframeApiPromise;
  }

  function setYouTubeEmbedState(section, state) {
    section.dataset.youtubeState = state;
    const loading = section.querySelector("[data-youtube-loading]");
    const player = section.querySelector("[data-youtube-player-slot]");
    const blocked = section.querySelector("[data-youtube-fallback-blocked]");
    const error = section.querySelector("[data-youtube-fallback-error]");
    if (!loading || !player || !blocked || !error) return;

    loading.classList.toggle("hidden", state !== "loading");
    player.classList.toggle("hidden", state !== "player");
    blocked.classList.toggle("hidden", state !== "blocked");
    error.classList.toggle("hidden", state !== "error");
  }

  function initYouTubeEmbed(section) {
    if (section.dataset.youtubeBound === "1") return;
    section.dataset.youtubeBound = "1";

    const videoId = (section.dataset.youtubeVideoId || "").trim();
    const slot = section.querySelector("[data-youtube-player-slot]");
    if (!videoId || !slot) return;

    setYouTubeEmbedState(section, "loading");

    const mount = async () => {
      try {
        await loadYouTubeIframeApi();
        const host = document.createElement("div");
        host.className = "h-full w-full";
        host.id = `yt-player-${videoId}-${Math.random().toString(36).slice(2, 8)}`;
        slot.replaceChildren(host);

        new window.YT.Player(host, {
          host: "https://www.youtube-nocookie.com",
          videoId,
          playerVars: {
            autoplay: 0,
            rel: 0,
            modestbranding: 1,
            playsinline: 1,
            origin: window.location.origin,
          },
          events: {
            onReady: () => {
              setYouTubeEmbedState(section, "player");
            },
            onError: (event) => {
              const code = Number(event?.data);
              if (code === 101 || code === 150) {
                setYouTubeEmbedState(section, "blocked");
                return;
              }
              setYouTubeEmbedState(section, "error");
            },
          },
        });
      } catch (_err) {
        setYouTubeEmbedState(section, "error");
      }
    };

    const startMount = () => {
      if (section.dataset.youtubeStarted === "1") return;
      section.dataset.youtubeStarted = "1";
      void mount();
    };

    if (!("IntersectionObserver" in window)) {
      startMount();
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        const isVisible = entries.some((entry) => entry.isIntersecting);
        if (!isVisible) return;
        observer.disconnect();
        startMount();
      },
      { rootMargin: "120px 0px" },
    );
    observer.observe(section);
  }

  function bindYouTubeEmbeds(scope) {
    const root = scope instanceof Element ? scope : document;
    root.querySelectorAll("[data-youtube-embed]").forEach(initYouTubeEmbed);
  }

  window.BrieftubeYoutubeEmbed = {
    bindYouTubeEmbeds,
  };
})();
