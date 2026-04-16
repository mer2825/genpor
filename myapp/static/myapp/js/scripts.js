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
    const redeemCouponUrl = window.DjangoContext.redeemCouponUrl;

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

    // --- LÓGICA SHOWCASE ---
    const showcasePrompts = window.showcasePrompts || [];
    const promptTextElement = document.getElementById('prompt-text');
    const showcaseImgElement = document.getElementById('showcase-img');

    if (showcasePrompts.length > 0 && promptTextElement && showcaseImgElement) {
        let currentIndex = 0;

        const typeWriter = (text, i, callback) => {
            if (i < text.length) {
                promptTextElement.innerHTML = text.substring(0, i + 1);
                setTimeout(() => typeWriter(text, i + 1, callback), 50); // Velocidad de tecleo
            } else if (callback) {
                setTimeout(callback, 2000); // Pausa antes de borrar
            }
        };

        const eraseText = (callback) => {
            const text = promptTextElement.innerHTML;
            if (text.length > 0) {
                promptTextElement.innerHTML = text.substring(0, text.length - 1);
                setTimeout(() => eraseText(callback), 30); // Velocidad de borrado
            } else if (callback) {
                callback();
            }
        };

        const cyclePrompts = () => {
            const currentItem = showcasePrompts[currentIndex];

            // Cambiar la imagen con transición
            showcaseImgElement.style.opacity = 0;
            showcaseImgElement.style.transform = 'scale(1.05)';

            setTimeout(() => {
                showcaseImgElement.src = currentItem.image;
                showcaseImgElement.style.opacity = 1;
                showcaseImgElement.style.transform = 'scale(1)';
            }, 500); // Sincronizado con la transición CSS

            // Iniciar la animación de tecleo
            typeWriter(currentItem.prompt, 0, () => {
                eraseText(() => {
                    currentIndex = (currentIndex + 1) % showcasePrompts.length;
                    cyclePrompts();
                });
            });
        };

        // Iniciar el ciclo
        cyclePrompts();
    }

    // --- AGE GATE LOGIC ---
    if (!localStorage.getItem('ageVerified')) {
        const overlay = document.getElementById('age-gate-overlay');
        if (overlay) {
            overlay.style.display = 'flex';
            document.body.style.overflow = 'hidden'; // Bloquear scroll
        }
    }

    // --- ANIMACIÓN ESTELAR DEL BOTÓN ---
    const heroBtn = document.getElementById('hero-cta-btn');
    const fabBtn = document.getElementById('fab-create-btn');
    const starParticle = document.getElementById('star-particle');
    const footer = document.querySelector('footer'); // Referencia al footer

    if (heroBtn && fabBtn && starParticle && footer) {
        let isFabVisible = false;
        let isAnimating = false;

        function getBaseBottom() {
             // En móvil (<= 768px), la base debe ser mayor para librar el bottom nav (aprox 60-70px)
             // Usamos 90px para estar seguros. En desktop 30px.
             return window.innerWidth <= 768 ? 90 : 30;
        }

        // NUEVA LÓGICA DE SCROLL
        function checkScroll() {
            const rect = heroBtn.getBoundingClientRect();
            const footerRect = footer.getBoundingClientRect();
            const windowHeight = window.innerHeight;

            // 1. Lógica de aparición (cuando el botón Hero sale de pantalla)
            if (rect.top < 150) {
                if (!isFabVisible && !isAnimating) {
                    heroBtn.classList.add('hidden-hero');
                    animateStarTransition();
                }
            } else {
                if (isFabVisible && rect.top > 300) {
                    hideFab();
                    heroBtn.classList.remove('hidden-hero');
                }
            }

            // 2. Lógica de "Empuje" por el Footer
            // Si el footer entra en la pantalla (su top es menor que la altura de la ventana)
            const currentBase = getBaseBottom();

            if (footerRect.top < windowHeight) {
                // Calculamos cuánto ha entrado el footer
                const overlap = windowHeight - footerRect.top;

                // Movemos el botón hacia arriba esa cantidad + el margen base
                // (overlap + baseBottom)
                fabBtn.style.bottom = `${currentBase + overlap}px`;
            } else {
                // Si el footer no está visible, volvemos a la posición original
                fabBtn.style.bottom = `${currentBase}px`;
            }
        }

        // Escuchar el evento scroll
        window.addEventListener('scroll', checkScroll);
        // Chequear al inicio por si ya está scrolleado
        checkScroll();

        function animateStarTransition() {
            isAnimating = true;

            // 1. Obtener posiciones
            const heroRect = heroBtn.getBoundingClientRect();
            const fabRect = fabBtn.getBoundingClientRect();

            // 2. Posicionar la estrella en el botón del Hero
            // (Usamos coordenadas fijas relativas a la ventana)
            const startX = heroRect.left + heroRect.width / 2;
            const startY = heroRect.top + heroRect.height / 2;

            // Destino: centro del FAB
            // Si el FAB está oculto (scale 0), getBoundingClientRect puede dar valores raros,
            // así que calculamos basándonos en su posición CSS fija (bottom: 30px, right: 30px)
            const endX = window.innerWidth - 30 - (fabRect.width / 2 || 60);
            const endY = window.innerHeight - 30 - (fabRect.height / 2 || 25);

            // 3. Configurar estado inicial de la estrella
            starParticle.style.left = `${startX}px`;
            starParticle.style.top = `${startY}px`;
            starParticle.style.opacity = '1';
            starParticle.style.transition = 'none'; // Resetear transición

            // Calcular ángulo para la cola de la estrella
            const deltaX = endX - startX;
            const deltaY = endY - startY;
            const angle = Math.atan2(deltaY, deltaX) * 180 / Math.PI;
            starParticle.style.transform = `translate(-50%, -50%) rotate(${angle}deg)`;

            // Forzar reflow
            void starParticle.offsetWidth;

            // 4. Iniciar animación de viaje
            starParticle.style.transition = 'all 0.6s cubic-bezier(0.5, 0, 0, 1)'; // Aceleración tipo cohete
            starParticle.style.left = `${endX}px`;
            starParticle.style.top = `${endY}px`;

            // 5. Al terminar el viaje
            setTimeout(() => {
                // Ocultar estrella
                starParticle.style.opacity = '0';

                // Mostrar FAB con efecto POP
                fabBtn.classList.add('visible');
                isFabVisible = true;
                isAnimating = false;
            }, 600); // Duración coincidente con la transición CSS
        }

        function hideFab() {
            fabBtn.classList.remove('visible');
            isFabVisible = false;
        }
    }
});

// Cerrar modal al hacer clic fuera
window.onclick = function(event) {
    if (event.target.classList.contains('modal')) {
        event.target.style.display = 'none';
    }
    // Cerrar modales legales también
    if (event.target.classList.contains('legal-modal-overlay')) {
        event.target.classList.remove('active');
        document.body.style.overflow = '';
    }
};

// --- GLOBAL FUNCTIONS FOR INLINE ONCLICK HANDLERS ---
window.enterSite = function() {
    localStorage.setItem('ageVerified', 'true');
    const overlay = document.getElementById('age-gate-overlay');
    if (overlay) overlay.style.display = 'none';
    document.body.style.overflow = ''; // Restaurar scroll
};

window.exitSite = function() {
    window.location.href = 'https://www.google.com';
};

window.openLegalModal = function(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.classList.add('active');
        document.body.style.overflow = 'hidden';
    }
};

window.closeLegalModal = function(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.classList.remove('active');
        document.body.style.overflow = '';
    }
};

window.filterCategory = function(categoryId, btn) {
    document.querySelectorAll('.filter-buttons-container .filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('personajes-list').dataset.activeCategory = categoryId;
    window.applyFilters();
};

window.toggleSubCategory = function(subId, btn) {
    // 1. Verificar si el botón ya estaba activo
    const wasActive = btn.classList.contains('active');

    // 2. Quitar 'active' de TODOS los botones de subcategoría
    document.querySelectorAll('.sub-filter-container .filter-btn').forEach(b => b.classList.remove('active'));

    // 3. Limpiar la lista de filtros
    const list = document.getElementById('personajes-list');
    list.dataset.activeSubs = '';

    // 4. Si NO estaba activo, lo activamos (si estaba activo, al hacer clic se desactiva y queda todo limpio)
    if (!wasActive) {
        btn.classList.add('active');
        list.dataset.activeSubs = subId;
    }

    // 5. Aplicar filtros
    window.applyFilters();
};

window.applyFilters = function() {
    const list = document.getElementById('personajes-list');
    const activeCategoryId = list.dataset.activeCategory || 'ALL';
    const activeSubs = list.dataset.activeSubs ? list.dataset.activeSubs.split(',').filter(Boolean) : [];
    const unlockSection = document.getElementById('unlock-section');

    const cards = document.querySelectorAll('.character-card');
    let visibleCount = 0;

    cards.forEach(card => {
        const itemCategoryId = card.getAttribute('data-category-id');
        const itemSubCategoryId = card.getAttribute('data-subcategory-id');
        const isPrivate = card.getAttribute('data-is-private') === 'true';

        let matchesCategory = false;

        if (activeCategoryId === 'PRIVATE') {
            // Si estamos en la pestaña PRIVATE, solo mostrar privados
            matchesCategory = isPrivate;
            // Mostrar botón de desbloqueo
            if (unlockSection) unlockSection.style.display = 'flex';
        } else {
            // Si estamos en ALL o una categoría normal
            if (activeCategoryId === 'ALL') {
                // Mostrar TODO (Públicos + Privados desbloqueados)
                matchesCategory = true;
            } else {
                // Filtrar por categoría específica
                matchesCategory = itemCategoryId === activeCategoryId;
            }
            // Ocultar botón de desbloqueo (solo visible en pestaña Private)
            if (unlockSection) unlockSection.style.display = 'none';
        }

        const matchesSub = activeSubs.length === 0 || activeSubs.includes(itemSubCategoryId);

        if (matchesCategory && matchesSub) {
            card.style.display = 'block';
            card.style.opacity = '0';
            card.style.transform = 'scale(0.95)';
            setTimeout(() => {
                card.style.opacity = '1';
                card.style.transform = 'scale(1)';
            }, 50);
            visibleCount++;
        } else {
            card.style.display = 'none';
        }
    });
};

window.unlockCharacter = function() {
    const codeInput = document.getElementById('unlock-code');
    const messageDiv = document.getElementById('unlock-message');
    if (!codeInput || !messageDiv) return;

    const code = codeInput.value.trim();

    if (!code) {
        messageDiv.style.color = '#ef4444';
        messageDiv.textContent = 'Please enter a key.';
        return;
    }

    const formData = new FormData();
    formData.append('code', code);

    // Get URL and token from global context
    const ctx = window.DjangoContext;
    if (!ctx) {
        messageDiv.style.color = '#ef4444';
        messageDiv.textContent = 'Configuration error. Please reload the page.';
        return;
    }

    fetch(ctx.redeemCouponUrl, {
        method: 'POST',
        headers: { 'X-CSRFToken': ctx.csrfToken, 'X-Requested-With': 'XMLHttpRequest' },
        body: new URLSearchParams(formData)
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === 'success') {
            document.getElementById('unlock-input-container').style.display = 'none';
            document.getElementById('unlock-footer').style.display = 'none';
            const successContainer = document.getElementById('unlock-success-container');
            if (successContainer) successContainer.style.display = 'flex';
            const successText = document.getElementById('unlock-success-text');
            if (successText) successText.textContent = data.message;

            // --- ANIMACIÓN DE ESTRELLAS MEJORADA (CONFETTI) ---
            const colors = ['#fbbf24', '#f59e0b', '#fb923c', '#ffffff', '#fcd34d'];

            for(let i=0; i<50; i++) {
                const star = document.createElement('i');
                star.classList.add('fas', 'fa-star', 'unlock-particle');

                // Random Position (Explosion)
                const angle = Math.random() * Math.PI * 2;
                const velocity = 50 + Math.random() * 150; // Varying distance
                const tx = Math.cos(angle) * velocity;
                const ty = Math.sin(angle) * velocity;

                // Random Rotation
                const rot = -200 + Math.random() * 400;

                // Random Size
                const scale = 0.5 + Math.random() * 1.0;

                // Random Color
                star.style.color = colors[Math.floor(Math.random() * colors.length)];
                star.style.fontSize = `${10 * scale}px`;

                // Set CSS Variables
                star.style.setProperty('--tx', `${tx}px`);
                star.style.setProperty('--ty', `${ty}px`);
                star.style.setProperty('--rot', `${rot}deg`);

                // Position relative to center of container
                star.style.left = '50%';
                star.style.top = '50%';

                // Animation
                star.style.animation = `confetti-explosion ${0.8 + Math.random() * 0.5}s ease-out forwards`;

                // CAMBIO: Añadir al BODY para evitar recortes
                document.body.appendChild(star);

                // Limpieza
                setTimeout(() => star.remove(), 1500);
            }

            // Recargar para mostrar el nuevo personaje
            setTimeout(() => window.location.reload(), 2500);
        } else {
            messageDiv.style.color = '#ef4444';
            messageDiv.textContent = data.message;
        }
    })
    .catch(() => {
        messageDiv.style.color = '#ef4444';
        messageDiv.textContent = 'Connection error.';
    });
};