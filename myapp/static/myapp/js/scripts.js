// Función global para los chips
function addTag(tag) {
    const textarea = document.getElementById('modal-prompt');
    if(textarea) {
        const currentVal = textarea.value.trim();
        if (currentVal) {
            textarea.value = currentVal + ', ' + tag;
        } else {
            textarea.value = tag;
        }
        textarea.focus();
    }
}

document.addEventListener('DOMContentLoaded', function() {
    // Obtener contexto desde variables globales definidas en el HTML
    // (Verificar si existen antes de usar)
    if (!window.DjangoContext) return;

    const userIsAuthenticated = window.DjangoContext.userIsAuthenticated;
    const loginUrl = window.DjangoContext.loginUrl;
    const generateUrl = window.DjangoContext.generateUrl;
    const csrfToken = window.DjangoContext.csrfToken;

    // --- LÓGICA CARRUSEL HERO (AUTOMÁTICO SIEMPRE) ---
    const heroSlides = document.querySelectorAll('.hero-slide');
    if (heroSlides.length > 1) {
        let heroIndex = 0;
        setInterval(() => {
            heroSlides[heroIndex].classList.remove('active');
            heroIndex = (heroIndex + 1) % heroSlides.length;
            heroSlides[heroIndex].classList.add('active');
        }, 4000);
    }

    // --- LÓGICA CARRUSEL TARJETAS (REACTIVO) ---
    const carousels = document.querySelectorAll('.carousel');

    // Almacenar referencias a los intervalos y estados de cada carrusel
    const carouselStates = new Map();

    carousels.forEach(carousel => {
        const images = carousel.querySelectorAll('.carousel-image');
        const segments = carousel.querySelectorAll('.progress-segment');

        // Inicializar estado para este carrusel
        carouselStates.set(carousel, {
            images: images,
            segments: segments,
            currentIndex: 0,
            autoInterval: null,   // Para modo móvil
            hoverInterval: null,  // Para modo escritorio
            isHovering: false
        });

        // Función auxiliar para actualizar vista
        const updateView = (state) => {
            state.images.forEach((img, i) => img.classList.toggle('active', i === state.currentIndex));
            state.segments.forEach((seg, i) => seg.classList.toggle('active', i === state.currentIndex));
        };

        // Eventos de Mouse (siempre se registran, pero su efecto depende del modo)
        // CORRECCIÓN: Asegurar que el evento se adjunte al contenedor padre (.character-card) para mejor UX
        const card = carousel.closest('.character-card');

        if (card) {
            card.addEventListener('mouseenter', () => {
                const state = carouselStates.get(carousel);
                state.isHovering = true;

                // Solo activar hover rápido si NO estamos en modo móvil
                if (window.innerWidth > 768) {
                    // Limpiar cualquier intervalo previo por seguridad
                    if (state.hoverInterval) clearInterval(state.hoverInterval);

                    // Iniciar rotación rápida
                    state.hoverInterval = setInterval(() => {
                        state.currentIndex = (state.currentIndex + 1) % state.images.length;
                        updateView(state);
                    }, 800); // Velocidad de rotación al pasar el mouse
                }
            });

            card.addEventListener('mouseleave', () => {
                const state = carouselStates.get(carousel);
                state.isHovering = false;

                // Limpiar hover interval
                if (state.hoverInterval) {
                    clearInterval(state.hoverInterval);
                    state.hoverInterval = null;
                }

                // Si es escritorio, resetear al salir para que siempre empiece desde la 1ra
                if (window.innerWidth > 768) {
                    state.currentIndex = 0;
                    updateView(state);
                }
            });
        }
    });

    // Función para gestionar el comportamiento según el tamaño de pantalla
    function manageCarouselBehavior() {
        const isMobile = window.innerWidth <= 768;

        carousels.forEach(carousel => {
            const state = carouselStates.get(carousel);
            if (state.images.length <= 1) return;

            if (isMobile) {
                // MODO MÓVIL: Activar autoplay lento si no existe
                if (!state.autoInterval) {
                    state.autoInterval = setInterval(() => {
                        state.currentIndex = (state.currentIndex + 1) % state.images.length;
                        // Usar la función updateView definida arriba (re-implementada aquí por scope o acceso al map)
                        state.images.forEach((img, i) => img.classList.toggle('active', i === state.currentIndex));
                        state.segments.forEach((seg, i) => seg.classList.toggle('active', i === state.currentIndex));
                    }, 2500);
                }

                // Asegurar que no haya hover interval residual
                if (state.hoverInterval) {
                    clearInterval(state.hoverInterval);
                    state.hoverInterval = null;
                }

            } else {
                // MODO ESCRITORIO: Desactivar autoplay móvil
                if (state.autoInterval) {
                    clearInterval(state.autoInterval);
                    state.autoInterval = null;

                    // Resetear a la primera imagen si no se está haciendo hover
                    if (!state.isHovering) {
                        state.currentIndex = 0;
                        state.images.forEach((img, i) => img.classList.toggle('active', i === 0));
                        state.segments.forEach((seg, i) => seg.classList.toggle('active', i === 0));
                    }
                }
            }
        });
    }

    // Ejecutar al inicio
    manageCarouselBehavior();

    // Ejecutar al redimensionar (con debounce opcional, aquí directo para simplicidad)
    window.addEventListener('resize', manageCarouselBehavior);


    // --- LÓGICA MODALES (SOLO PARA HOME/CATÁLOGO) ---
    // NOTA: En Workspace, la lógica de modales está en el propio HTML para evitar conflictos.
    const generationModal = document.getElementById('generation-modal');
    const galleryModal = document.getElementById('gallery-modal');
    let currentCharacterId = null;

    if (generationModal || galleryModal) {
        const setupModal = (modal, openBtnsSelector, titleSelector, cardSelector, onOpen) => {
            if (!modal) return;

            const closeBtn = modal.querySelector('.close-btn');
            document.querySelectorAll(openBtnsSelector).forEach(btn => {
                btn.addEventListener('click', event => {
                    if (!userIsAuthenticated) {
                        window.location.href = loginUrl;
                        return;
                    }

                    const card = event.target.closest(cardSelector);
                    const characterName = card.querySelector('h2').textContent;
                    currentCharacterId = card.dataset.characterId;
                    modal.querySelector(titleSelector).textContent = `${modal.id === 'gallery-modal' ? 'Galería de' : 'Generar para'} ${characterName}`;
                    if (onOpen) onOpen(card);
                    modal.style.display = 'block';
                });
            });
            if (closeBtn) closeBtn.onclick = () => modal.style.display = 'none';
        };

        setupModal(generationModal, '.generate-btn', '#modal-title', '.character-card', () => {
            document.getElementById('modal-prompt').value = '';
            document.getElementById('modal-result').innerHTML = '';
            document.getElementById('modal-loader').style.display = 'none';
            document.getElementById('modal-generate-btn').style.display = 'flex';
        });

        setupModal(galleryModal, '.gallery-btn', '#gallery-modal-title', '.character-card', () => {
            const grid = galleryModal.querySelector('.image-grid');
            grid.innerHTML = '<p style="text-align:center; color: var(--text-muted);">Cargando...</p>';
            fetch(`${generateUrl}?character_id=${currentCharacterId}`, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
                .then(res => res.json())
                .then(data => {
                    grid.innerHTML = '';
                    if (data.status === 'success' && data.images.length > 0) {
                        data.images.forEach(url => {
                            const img = document.createElement('img');
                            img.src = url;
                            img.onclick = () => window.open(url, '_blank');
                            grid.appendChild(img);
                        });
                    } else {
                        grid.innerHTML = '<p style="text-align:center; color: var(--text-muted);">No hay imágenes generadas por ti para este personaje.</p>';
                    }
                });
        });

        // --- CORRECCIÓN: Usar addEventListener en lugar de sobrescribir window.onclick ---
        window.addEventListener('click', (event) => {
            if (generationModal && event.target == generationModal) generationModal.style.display = 'none';
            if (galleryModal && event.target == galleryModal) galleryModal.style.display = 'none';
        });

        const generateBtn = document.getElementById('modal-generate-btn');
        if (generateBtn) {
            generateBtn.addEventListener('click', () => {
                const prompt = document.getElementById('modal-prompt').value;
                if (!prompt) { alert('Por favor, escribe un prompt.'); return; }

                const loader = document.getElementById('modal-loader');
                const resultDiv = document.getElementById('modal-result');
                const btn = document.getElementById('modal-generate-btn');

                loader.style.display = 'block';
                resultDiv.innerHTML = '';
                btn.style.display = 'none';

                const formData = new FormData();
                formData.append('character_id', currentCharacterId);
                formData.append('prompt', prompt);

                fetch(generateUrl, {
                    method: 'POST',
                    headers: { 'X-CSRFToken': csrfToken, 'X-Requested-With': 'XMLHttpRequest' },
                    body: new URLSearchParams(formData)
                })
                .then(response => response.json())
                .then(data => {
                    if (data.status === 'success') {
                        let html = '<p style="color: var(--success-color); text-align: center; font-weight: bold;">¡Imagen completada!</p>';
                        if (data.image_urls && data.image_urls.length > 0) {
                            data.image_urls.forEach(url => {
                                html += `<img src="${url}" alt="Imagen generada">`;
                            });
                        } else if (data.image_url) {
                            html += `<img src="${data.image_url}" alt="Imagen generada">`;
                        }
                        resultDiv.innerHTML = html;
                    } else {
                        resultDiv.innerHTML = `<p style="color: #ef4444; text-align: center;">Error: ${data.message}</p>`;
                    }
                })
                .catch(error => {
                    console.error('Error:', error);
                    resultDiv.innerHTML = `<p style="color: #ef4444; text-align: center;">Ocurrió un error de conexión.</p>`;
                })
                .finally(() => {
                    loader.style.display = 'none';
                    btn.style.display = 'flex';
                });
            });
        }
    }
});
