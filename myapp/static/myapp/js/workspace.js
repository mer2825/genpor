// Cargar configuración inyectada desde Django
const {
    generateUrl,
    generateVideoUrl,
    deleteMsgUrl,
    clearChatUrl,
    redeemCouponUrl,
    csrfToken,
    characterId,
    canUpscale,
    canFaceDetail,
    canEyeDetail,
    isImageEnabled,
    isVideoEnabled
} = window.WORKSPACE_CONFIG || {};

// --- UI LOGIC ---
const textarea = document.getElementById('prompt-input');
const chatAreaImage = document.getElementById('chat-area-image');
const chatAreaVideo = document.getElementById('chat-area-video');
const bottomNav = document.querySelector('.bottom-nav');
const inputContainer = document.querySelector('.input-container');
const scrollBottomBtn = document.getElementById('scroll-bottom-btn');

// --- FUNCIONES DEL DROPDOWN DE SETTINGS (REFACTORIZADO) ---
function toggleDropdownMenu(menuId, btn) {
    const targetMenu = document.getElementById(menuId);
    if (!targetMenu) return;

    const isOpening = !targetMenu.classList.contains('show');

    // Cerrar todos los menús y desactivar botones
    document.querySelectorAll('.settings-dropdown-menu.show').forEach(menu => {
        menu.classList.remove('show');
    });
    document.querySelectorAll('.settings-dropdown-btn.active').forEach(b => {
        b.classList.remove('active');
    });

    // Si estamos abriendo un nuevo menú, mostrarlo y activar su botón
    if (isOpening) {
        targetMenu.classList.add('show');
        btn.classList.add('active');
    }
}

// Cerrar dropdowns al hacer clic fuera
document.addEventListener('click', function(e) {
    if (!e.target.closest('.dropdown-wrapper')) {
        document.querySelectorAll('.settings-dropdown-menu.show').forEach(menu => {
            menu.classList.remove('show');
        });
        document.querySelectorAll('.settings-dropdown-btn.active').forEach(btn => {
            btn.classList.remove('active');
        });
    }
});


// --- MODE SWITCH LOGIC ---
// Si isImageEnabled es false, forzamos modo video si está habilitado, sino defaults a image
let currentMode = (isImageEnabled || (!isImageEnabled && !isVideoEnabled)) ? 'image' : 'video';

function setMode(mode) {
    // Si se intenta cambiar a un modo deshabilitado y el otro está habilitado, forzar al habilitado
    if (mode === 'image' && !isImageEnabled && isVideoEnabled) {
        mode = 'video';
    } else if (mode === 'video' && !isVideoEnabled && isImageEnabled) {
        mode = 'image';
    }

    currentMode = mode;
    localStorage.setItem('workspaceMode', mode); // GUARDAR MODO

    // --- NUEVO: Actualizar el interruptor de escritorio ---
    const desktopImageBtn = document.getElementById('mode-btn-image');
    const desktopVideoBtn = document.getElementById('mode-btn-video');
    if (desktopImageBtn && desktopVideoBtn) {
        desktopImageBtn.classList.toggle('active', mode === 'image');
        desktopVideoBtn.classList.toggle('active', mode === 'video');
    }

    // Mostrar/Ocultar botón de aspect ratio (SOLO IMAGEN)
    const aspectDropdownWrapper = document.getElementById('aspect-dropdown-wrapper');
    if (aspectDropdownWrapper) {
        aspectDropdownWrapper.style.display = (mode === 'image' && isImageEnabled) ? 'block' : 'none';
    }

    // Mostrar/Ocultar botón de video settings (SOLO VIDEO)
    const videoSettingsDropdownWrapper = document.getElementById('video-settings-dropdown-wrapper');
    if (videoSettingsDropdownWrapper) {
        videoSettingsDropdownWrapper.style.display = (mode === 'video' && isVideoEnabled) ? 'block' : 'none';
    }

    // Toggle chat areas (también asegurando que respete la habilitación)
    if(chatAreaImage) {
        if(mode === 'image' && isImageEnabled) {
            chatAreaImage.style.display = 'flex';
        } else {
            chatAreaImage.style.display = 'none';
        }
    }

    if(chatAreaVideo) {
        if(mode === 'video' && isVideoEnabled) {
            chatAreaVideo.style.display = 'flex';
        } else {
            chatAreaVideo.style.display = 'none';
        }
    }

    // Toggle image selector in input area
    const videoSourceContainer = document.getElementById('video-source-container');
    if (videoSourceContainer) {
        videoSourceContainer.style.display = (mode === 'video' && isVideoEnabled) ? 'block' : 'none';
    }

    // Update placeholder
    if (textarea) {
        if (mode === 'image' && isImageEnabled) {
            textarea.placeholder = "She is...";
        } else if (mode === 'video' && isVideoEnabled) {
            textarea.placeholder = "Describe the motion...";
        } else {
            textarea.placeholder = "Generation disabled...";
        }
    }

    // Actualizar el ícono del botón de cambio de modo móvil
    const modeIcon = document.getElementById('mode-toggle-icon');
    if (modeIcon) {
        if (mode === 'image') {
            modeIcon.className = 'fas fa-image';
        } else {
            modeIcon.className = 'fas fa-film';
        }
    }

    // Check if mode is disabled
    const isDisabled = (mode === 'image' && !isImageEnabled) || (mode === 'video' && !isVideoEnabled);

    const sendBtn = document.getElementById('send-btn');
    if (textarea) {
        textarea.disabled = isDisabled;
        // Restaurar estilos cuando esté habilitado/deshabilitado
        textarea.style.opacity = isDisabled ? '0.5' : '1';
        if (!isDisabled) {
            textarea.style.height = 'auto'; // Resetear para calcularscrollHeight
            // Usamos un pequeño delay porque a veces el DOM necesita un frame para renderizar el textarea correctamente
            setTimeout(() => {
                textarea.style.height = Math.min(textarea.scrollHeight || 24, 120) + 'px';
            }, 10);
        }
    }

    if (sendBtn) {
        sendBtn.disabled = isDisabled;
        sendBtn.style.opacity = isDisabled ? '0.5' : '1';
    }

    const existingMsg = document.getElementById('disabled-mode-msg');
    if (existingMsg) existingMsg.remove();

    if (isDisabled) {
        const activeChat = getActiveChatArea();
        if (activeChat) {
            const msg = document.createElement('div');
            msg.id = 'disabled-mode-msg';
            msg.className = 'message ai';
            msg.style.color = '#ef4444';
            msg.style.fontWeight = 'bold';
            msg.style.border = '1px solid #ef4444';
            msg.style.alignSelf = 'center';
            msg.style.margin = '20px auto';
            msg.innerHTML = `⚠️ The ${mode} generation is currently disabled by the administrator.`;
            activeChat.appendChild(msg);
        }
    }

    scrollToBottom();
}

// Auto-resize Textarea
if (textarea) {
    textarea.addEventListener('input', function() {
        this.style.height = 'auto';
        this.style.height = Math.min(this.scrollHeight, 120) + 'px';
    });
}

// Scroll al fondo al cargar y restaurar modo
window.onload = function() {
    // Restaurar modo guardado
    let savedMode = localStorage.getItem('workspaceMode');

    // Si tenemos modo guardado pero está deshabilitado
    if (savedMode === 'image' && !isImageEnabled && isVideoEnabled) {
        savedMode = 'video';
    } else if (savedMode === 'video' && !isVideoEnabled && isImageEnabled) {
        savedMode = 'image';
    }

    if (savedMode && (savedMode === 'image' || savedMode === 'video')) {
        setMode(savedMode);
    } else {
        setMode((isImageEnabled || (!isImageEnabled && !isVideoEnabled)) ? 'image' : 'video');
    }

    // Solucionar el problema de altura inicial del textarea
    // A veces al cargar la página, el scrollHeight puede calcularse mal si no hay contenido.
    if (textarea && !textarea.disabled) {
        // Fijamos una altura inicial forzada primero, para evitar que suba.
        textarea.style.height = 'auto';
        setTimeout(() => {
            textarea.style.height = Math.min(textarea.scrollHeight || 24, 120) + 'px';
        }, 50); // Darle un poco más de tiempo de renderizado
    }

    createAnimatedShapes();
    scrollToBottom();
};

// Mobile Keyboard Handling
if (window.innerWidth <= 768 && textarea) {
    textarea.addEventListener('focus', () => {
        setTimeout(() => scrollToBottom(), 300);
    });
}

function getActiveChatArea() {
    return currentMode === 'image' ? chatAreaImage : chatAreaVideo;
}

function scrollToBottom() {
    const activeChat = getActiveChatArea();
    if (!activeChat) return;

    // En móvil, el scroll es del body/main-content, no de chatArea
    if (window.innerWidth <= 768) {
        window.scrollTo(0, document.body.scrollHeight);
        const mainContent = document.querySelector('.main-content');
        if(mainContent) mainContent.scrollTop = mainContent.scrollHeight;
    } else {
        activeChat.scrollTop = activeChat.scrollHeight;
    }
};

function scrollToBottomSmooth() {
    const activeChat = getActiveChatArea();
    if (!activeChat) return;

    if (window.innerWidth <= 768) {
        window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
        const mainContent = document.querySelector('.main-content');
        if(mainContent) mainContent.scrollTo({ top: mainContent.scrollHeight, behavior: 'smooth' });
    } else {
        activeChat.scrollTo({ top: activeChat.scrollHeight, behavior: 'smooth' });
    }
};

// Detectar scroll para mostrar/ocultar botón
function handleScroll() {
    const activeChat = getActiveChatArea();
    if (!activeChat) return;

    let scrollTop, scrollHeight, clientHeight;

    if (window.innerWidth <= 768) {
        // En móvil, el scroll principal suele ser window o main-content
        const mainContent = document.querySelector('.main-content');
        if(!mainContent) return;
        scrollTop = mainContent.scrollTop || window.scrollY;
        scrollHeight = mainContent.scrollHeight || document.body.scrollHeight;
        clientHeight = mainContent.clientHeight || window.innerHeight;
    } else {
        scrollTop = activeChat.scrollTop;
        scrollHeight = activeChat.scrollHeight;
        clientHeight = activeChat.clientHeight;
    }

    // Si estamos a más de 300px del final, mostrar botón
    if (scrollBottomBtn) {
        if (scrollHeight - scrollTop - clientHeight > 300) {
            scrollBottomBtn.style.display = 'flex';
        } else {
            scrollBottomBtn.style.display = 'none';
        }
    }
};

// Asignar listeners de scroll
if(chatAreaImage) chatAreaImage.addEventListener('scroll', handleScroll);
if(chatAreaVideo) chatAreaVideo.addEventListener('scroll', handleScroll);
window.addEventListener('scroll', handleScroll);
const mainContent = document.querySelector('.main-content');
if(mainContent) mainContent.addEventListener('scroll', handleScroll);


// Sidebars
function toggleSidebar(side) {
    const el = document.getElementById(`sidebar-${side}`);
    const overlay = document.getElementById('sidebar-overlay');
    if (!el || !overlay) return;

    const other = side === 'left' ? 'right' : 'left';

    if(document.getElementById(`sidebar-${other}`)) {
        document.getElementById(`sidebar-${other}`).classList.remove('open');
    }

    if (el.classList.contains('open')) {
        el.classList.remove('open');
        overlay.classList.remove('show');
        setTimeout(() => overlay.style.display = 'none', 300);
    } else {
        overlay.style.display = 'block';
        setTimeout(() => overlay.classList.add('show'), 10);
        el.classList.add('open');
    }
};

function closeAllSidebars() {
    if(document.getElementById('sidebar-left')) document.getElementById('sidebar-left').classList.remove('open');
    if(document.getElementById('sidebar-right')) document.getElementById('sidebar-right').classList.remove('open');
    const overlay = document.getElementById('sidebar-overlay');
    if(overlay) {
        overlay.classList.remove('show');
        setTimeout(() => overlay.style.display = 'none', 300);
    }
};

// Modales
const btnRef = document.getElementById('btn-show-reference');
const btnGalImg = document.getElementById('btn-show-gallery-images');
const btnGalVid = document.getElementById('btn-show-gallery-videos');

if(btnRef) btnRef.onclick = () => {
    const modalRef = document.getElementById('modal-reference');
    if (modalRef) modalRef.style.display = 'block';
};

if(btnGalImg) btnGalImg.onclick = () => {
    const modalGal = document.getElementById('modal-gallery');
    const modalGalTitle = document.getElementById('modal-gallery-title');
    if (modalGal) modalGal.style.display = 'block';
    if (modalGalTitle) modalGalTitle.textContent = "Image Gallery";
    loadUserGallery('image');
};

if(btnGalVid) btnGalVid.onclick = () => {
    const modalGal = document.getElementById('modal-gallery');
    const modalGalTitle = document.getElementById('modal-gallery-title');
    if (modalGal) modalGal.style.display = 'block';
    if (modalGalTitle) modalGalTitle.textContent = "Video Gallery";
    loadUserGallery('video');
};

// --- CERRAR MODALES AL CLIC FUERA (ROBUSTO) ---
document.querySelectorAll('.modal').forEach(modal => {
    modal.addEventListener('click', function(event) {
        if (event.target === modal) {
            modal.style.display = 'none';
        }
    });
});

// --- ASPECT RATIO LOGIC ---
const ratioContainer = document.querySelector('.aspect-ratio-grid');
const widthInput = document.getElementById('width-input');
const heightInput = document.getElementById('height-input');

document.addEventListener('DOMContentLoaded', () => {
    if(ratioContainer && widthInput && heightInput) {
        const buttons = ratioContainer.querySelectorAll('.aspect-ratio-btn');
        let defaultFound = false;
        buttons.forEach(btn => {
            if (btn.dataset.width === widthInput.value && btn.dataset.height === heightInput.value) {
                btn.classList.add('active');
                defaultFound = true;
            }
        });
        if (!defaultFound && buttons.length > 0) {
            const firstButton = buttons[0];
            firstButton.classList.add('active');
            widthInput.value = firstButton.dataset.width;
            heightInput.value = firstButton.dataset.height;
        }
    }
});

if(ratioContainer) {
    ratioContainer.addEventListener('click', (e) => {
        const clickedButton = e.target.closest('.aspect-ratio-btn');
        if (!clickedButton) return;
        ratioContainer.querySelectorAll('.aspect-ratio-btn').forEach(btn => btn.classList.remove('active'));
        clickedButton.classList.add('active');
        if (widthInput && heightInput) {
            widthInput.value = clickedButton.dataset.width;
            heightInput.value = clickedButton.dataset.height;
        }

        // Actualizar texto del botón dropdown
        const aspectText = clickedButton.querySelector('span').innerText;
        const aspectBtn = document.getElementById('btn-toggle-aspect');
        if (aspectBtn) {
            aspectBtn.innerHTML = `<i class="fas fa-crop-alt"></i> ${aspectText} <i class="fas fa-chevron-down" style="font-size: 0.7em;"></i>`;
        }

        // Cerrar el panel automáticamente al seleccionar
        const targetMenu = document.getElementById('menu-aspect');
        if (targetMenu) targetMenu.classList.remove('show');
        if (aspectBtn) aspectBtn.classList.remove('active');
    });
}

// --- IMAGE SELECTION LOGIC (VIDEO) ---
let selectedImageBlob = null; // Para guardar el blob de la imagen seleccionada

function openImageSelectModal() {
    const modalImageSelect = document.getElementById('modal-image-select');
    if(modalImageSelect) modalImageSelect.style.display = 'block';

    const grid = document.getElementById('image-select-grid');
    if(!grid) return;

    grid.innerHTML = '<p style="color:gray;">Loading gallery...</p>';

    // Cargar galería del usuario
    fetch(`${generateUrl}?character_id=${characterId}`, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
    .then(res => res.json())
    .then(data => {
        grid.innerHTML = '';
        if(data.images && data.images.length > 0) {
            data.images.forEach(url => {
                const div = document.createElement('div');
                div.className = 'gallery-item-select';
                div.innerHTML = `<img src="${url}">`;
                div.onclick = () => selectImageForVideo(url);
                grid.appendChild(div);
            });
        } else {
            grid.innerHTML = '<p style="color:gray;">No images found. Generate some images first!</p>';
        }
    });
}

function selectImageForVideo(url) {
    const selectedImageUrlEl = document.getElementById('selected-image-url');
    if(selectedImageUrlEl) selectedImageUrlEl.value = url;

    // Actualizar vista previa
    const previewImg = document.getElementById('video-src-img');
    if (previewImg) previewImg.src = url;

    // Cambiar visibilidad: Ocultar botón, mostrar preview
    const btnSelect = document.getElementById('btn-select-video-source');
    if(btnSelect) btnSelect.style.display = 'none';
    const previewContainer = document.getElementById('preview-video-source');
    if(previewContainer) previewContainer.style.display = 'block';

    const modalImageSelect = document.getElementById('modal-image-select');
    if(modalImageSelect) modalImageSelect.style.display = 'none';

    // Convertir URL a Blob para enviarlo como archivo
    fetch(url)
        .then(res => res.blob())
        .then(blob => {
            selectedImageBlob = blob;
        });
}

function clearVideoSelection() {
    selectedImageBlob = null;
    const selectedImageUrlEl = document.getElementById('selected-image-url');
    if(selectedImageUrlEl) selectedImageUrlEl.value = '';

    // Restaurar visibilidad: Mostrar botón, ocultar preview
    const btnSelect = document.getElementById('btn-select-video-source');
    if(btnSelect) btnSelect.style.display = 'flex';
    const previewContainer = document.getElementById('preview-video-source');
    if(previewContainer) previewContainer.style.display = 'none';
}

// --- NUEVA FUNCIÓN PARA SELECCIONAR OPCIONES DE VIDEO ---
function selectVideoOption(type, value, btn) {
    // Actualizar input hidden
    const videoInput = document.getElementById(`video-${type}`);
    if(videoInput) videoInput.value = value;

    // Actualizar clases visuales
    const container = btn.parentElement;
    if(container) {
        container.querySelectorAll('.video-option-btn').forEach(b => b.classList.remove('active'));
    }
    btn.classList.add('active');
}

// --- TOAST NOTIFICATION FUNCTION (NUEVO) ---
function showToast(message, type = 'info') {
    let container = document.querySelector('.toast-container');
    if (!container) {
        container = document.createElement('div');
        container.className = 'toast-container';
        document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;

    let icon = '';
    if (type === 'error') icon = '<i class="fas fa-exclamation-circle"></i>';
    else if (type === 'success') icon = '<i class="fas fa-check-circle"></i>';
    else icon = '<i class="fas fa-info-circle"></i>';

    toast.innerHTML = `${icon}<span>${message}</span>`;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.animation = 'fadeOut 0.3s ease-in forwards';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// --- GENERATION LOGIC ---
let isGeneratingImage = false; // Flag para imágenes
let isGeneratingVideo = false; // Flag para videos

function sendPrompt() {
    // CERRAR PANELES DE SETTINGS
    document.querySelectorAll('.settings-dropdown-menu.show').forEach(menu => menu.classList.remove('show'));
    document.querySelectorAll('.settings-dropdown-btn.active').forEach(btn => btn.classList.remove('active'));

    if (currentMode === 'image' && !isImageEnabled) {
        showToast("Image generation is disabled.", "error");
        return;
    }
    if (currentMode === 'video' && !isVideoEnabled) {
        showToast("Video generation is disabled.", "error");
        return;
    }

    if (currentMode === 'image' && isGeneratingImage) return;
    if (currentMode === 'video' && isGeneratingVideo) return;

    const prompt = textarea ? textarea.value.trim() : "";
    if (!prompt || !characterId) return;

    if (currentMode === 'video' && !selectedImageBlob) {
        showToast("Please select a source image for the video.", "error");
        return;
    }

    if (currentMode === 'image') isGeneratingImage = true;
    else isGeneratingVideo = true;

    const sendBtn = document.getElementById('send-btn');
    const activeChat = getActiveChatArea();
    if(!activeChat) return;

    const userMsg = document.createElement('div');
    userMsg.className = 'message user';
    userMsg.textContent = prompt;
    activeChat.appendChild(userMsg);

    if (textarea) {
        textarea.value = '';
        textarea.style.height = '24px'; // reset height explicitly
    }
    scrollToBottom();

    const loaderMsg = document.createElement('div');
    loaderMsg.className = 'message ai loading-bubble';
    loaderMsg.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> ' + (currentMode === 'video' ? 'Generating video (this may take a while)...' : 'Creating your image...');
    activeChat.appendChild(loaderMsg);
    scrollToBottom();

    const formData = new FormData();
    formData.append('character_id', characterId);
    formData.append('prompt', prompt);
    formData.append('seed', '-1'); // Seed se maneja en backend

    let targetUrl = generateUrl;

    if (currentMode === 'image') {
        const widthInput = document.getElementById('width-input');
        const heightInput = document.getElementById('height-input');
        const qualityInput = document.getElementById('quality-input'); // LEER VALOR DE CALIDAD
        formData.append('width', widthInput ? widthInput.value : '1024');
        formData.append('height', heightInput ? heightInput.value : '1024');
        formData.append('quality', qualityInput ? qualityInput.value : 'STANDARD'); // AÑADIR CALIDAD AL FORMDATA

        let bestQuality = 'Gen_Normal';
        if (canEyeDetail) bestQuality = 'Gen_EyeDetailer';
        else if (canFaceDetail) bestQuality = 'Gen_FaceDetailer';
        else if (canUpscale) bestQuality = 'Gen_UpScaler';

        formData.append('generation_type', bestQuality);
    } else {
        targetUrl = generateVideoUrl;
        formData.append('image', selectedImageBlob, "source_image.png");
        const videoDuration = document.getElementById('video-duration');
        const videoQuality = document.getElementById('video-quality');
        if(videoDuration) formData.append('duration', videoDuration.value);
        if(videoQuality) formData.append('quality', videoQuality.value);
    }

    const mediaType = currentMode === 'image' ? 'image' : 'video';
    fetch(`${generateUrl}?character_id=${characterId}&media_type=${mediaType}`, {
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
    })
    .then(res => res.json())
    .then(countData => {
        let dbCount = 0;
        if (mediaType === 'image' && countData.images) dbCount = countData.images.length;
        else if (mediaType === 'video' && countData.videos) dbCount = countData.videos.length;

        const generationState = {
            isGenerating: true,
            characterId: characterId,
            mode: currentMode,
            timestamp: Date.now(),
            initialCount: dbCount
        };
        localStorage.setItem('pendingGeneration', JSON.stringify(generationState));

        doGenerationRequest(prompt, formData, activeChat, userMsg, loaderMsg);
    })
    .catch(err => {
        console.error("Error fetching initial count:", err);
        const domCount = document.querySelectorAll(currentMode === 'image' ? '.chat-image' : '.chat-video').length;
        const generationState = {
            isGenerating: true,
            characterId: characterId,
            mode: currentMode,
            timestamp: Date.now(),
            initialCount: domCount
        };
        localStorage.setItem('pendingGeneration', JSON.stringify(generationState));
        doGenerationRequest(prompt, formData, activeChat, userMsg, loaderMsg);
    });

    function doGenerationRequest(prompt, formData, activeChat, userMsg, loaderMsg) {
        fetch(targetUrl, {
            method: 'POST',
            headers: { 'X-CSRFToken': csrfToken, 'X-Requested-With': 'XMLHttpRequest' },
            body: formData
        })
        .then(res => res.json())
        .then(data => {
            if(activeChat.contains(loaderMsg)) {
                activeChat.removeChild(loaderMsg);
            }
            localStorage.removeItem('pendingGeneration');

            if (data.status === 'success') {
                const resultMsg = document.createElement('div');
                resultMsg.className = 'message ai';

                if (currentMode === 'image') {
                    userMsg.setAttribute('data-msg-id', data.user_msg_id);
                    resultMsg.setAttribute('data-msg-id', data.ai_msg_id);
                    let html = 'Here are your generated images.<br><div class="result-grid">';
                    data.results.forEach(item => {
                        let badgeText = "NORMAL";
                        let badgeClass = "badge-normal";
                        if (item.type === "Gen_UpScaler" || item.type === "UPSCALER") { badgeText = "UPSCALER"; badgeClass = "badge-upscale"; }
                        else if (item.type === "Gen_FaceDetailer" || item.type === "FACEDETAILER") { badgeText = "FACEDETAILER"; badgeClass = "badge-face"; }
                        else if (item.type === "Gen_EyeDetailer" || item.type === "EYEDETAILER") { badgeText = "EYEDETAILER"; badgeClass = "badge-face"; }

                        let resBadge = '';
                        if (item.width && item.height) { resBadge = `<span class="resolution-badge">${item.width}x${item.height}</span>`; }
                        html += `<div class="result-card"><span class="result-badge ${badgeClass}">${badgeText}</span>${resBadge}<img src="${item.url}" class="chat-image" onclick="openImageViewer(this.src)"></div>`;
                    });
                    html += '</div>';
                    resultMsg.innerHTML = html;
                } else {
                    userMsg.setAttribute('data-msg-id', data.user_msg_id);
                    resultMsg.setAttribute('data-msg-id', data.ai_msg_id);
                    let html = 'Here is your generated video.<br>';
                    html += `<div class="result-card">
                                <video src="${data.video_url}" class="chat-video" style="width:100%; border-radius:8px;" onclick="openVideoViewer(this.src)"></video>
                             </div>`;
                    resultMsg.innerHTML = html;
                }

                activeChat.appendChild(resultMsg);
            } else {
                const err = document.createElement('div');
                err.className = 'message ai';
                err.style.color = '#ef4444';
                err.textContent = 'Error: ' + data.message;
                activeChat.appendChild(err);
            }
            scrollToBottom();
        })
        .catch((e) => {
            console.error("Fetch error (possibly reload):", e);
            if(document.body.contains(loaderMsg)) {
                loaderMsg.innerHTML = '<i class="fas fa-wifi"></i> Connection interrupted. Checking status...';
            }
            checkPendingStatus();
        })
        .finally(() => {
            if (currentMode === 'image') isGeneratingImage = false;
            else isGeneratingVideo = false;

            if (sendBtn) {
                sendBtn.disabled = false;
                sendBtn.style.opacity = '1';
            }
            if (textarea) {
                textarea.disabled = false;
                textarea.focus();
            }
        });
    }
};

// --- NUEVO: SISTEMA DE PERSISTENCIA Y POLLING (DEPURADO) ---
function checkPendingStatus() {
    console.log("🔍 Checking pending status...");
    const savedState = localStorage.getItem('pendingGeneration');

    if (!savedState) {
        console.log("❌ No pending generation found in LocalStorage.");
        return;
    }

    try {
        const state = JSON.parse(savedState);
        console.log("📦 Found state:", state);
        console.log("🆔 Current Character ID:", characterId);

        // 1. Validación estricta de personaje
        if (String(state.characterId) !== String(characterId)) {
            console.log("⚠️ Character ID mismatch. Ignoring.");
            return;
        }

        // 2. Verificar timeout (10 minutos)
        const tenMinutes = 10 * 60 * 1000;
        if (Date.now() - state.timestamp > tenMinutes) {
            console.log("⏰ Timeout reached. Clearing state.");
            localStorage.removeItem('pendingGeneration');
            return;
        }

        // 3. Restaurar modo
        if (state.mode && state.mode !== currentMode) {
            console.log("🔄 Switching mode to:", state.mode);
            setMode(state.mode);
        }

        const activeChat = state.mode === 'image' ? document.getElementById('chat-area-image') : document.getElementById('chat-area-video');

        // 4. Restaurar UI
        if (activeChat && !activeChat.querySelector('.loading-bubble')) {
            console.log("✨ Restoring loading bubble UI...");
            const loaderMsg = document.createElement('div');
            loaderMsg.className = 'message ai loading-bubble';
            loaderMsg.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> ' + (state.mode === 'video' ? 'Generating video (resumed)...' : 'Creating your image (resumed)...');
            activeChat.appendChild(loaderMsg);
            scrollToBottom();
        }

        // 5. Polling
        console.log("📡 Starting polling...");
        const pollInterval = setInterval(() => {
            fetch(`${generateUrl}?character_id=${characterId}&media_type=${state.mode}`, {
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            })
            .then(res => res.json())
            .then(data => {
                let currentCount = 0;
                if (state.mode === 'image' && data.images) currentCount = data.images.length;
                else if (state.mode === 'video' && data.videos) currentCount = data.videos.length;

                console.log(`📊 Polling: Initial ${state.initialCount} vs Current ${currentCount}`);

                if (currentCount > state.initialCount) {
                    console.log("✅ New content detected! Finishing.");
                    clearInterval(pollInterval);
                    localStorage.removeItem('pendingGeneration');

                    const bubble = activeChat.querySelector('.loading-bubble');
                    if (bubble) bubble.remove();

                    const resultMsg = document.createElement('div');
                    resultMsg.className = 'message ai';
                    resultMsg.innerHTML = `Generation complete! <a href="javascript:window.location.reload()" style="color: #60a5fa; text-decoration: underline;">Click here to see it</a>.`;
                    activeChat.appendChild(resultMsg);
                    scrollToBottom();

                    setTimeout(() => window.location.reload(), 1000);
                }
            })
            .catch(err => console.error("Polling error:", err));
        }, 4000);

    } catch (e) {
        console.error("Error parsing state:", e);
    }
}

// Ejecutar inmediatamente
checkPendingStatus();

const sendBtn = document.getElementById('send-btn');
if (sendBtn) {
    sendBtn.addEventListener('click', sendPrompt);
}

if (textarea) {
    textarea.addEventListener('keypress', (e) => {
        if(e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendPrompt(); }
    });
}

// --- DELETE LOGIC ---
const deleteModal = document.getElementById('modal-delete');
const deleteModalText = document.getElementById('delete-modal-text');
const confirmDeleteBtn = document.getElementById('confirm-delete-btn');
const deleteImagesCheckbox = document.getElementById('delete-images-checkbox');
let messageToDelete = null;
let isClearingChat = false;

function openDeleteModal(msgId, clearChat = false) {
    messageToDelete = msgId;
    isClearingChat = clearChat;
    if (clearChat) {
        if(deleteModalText) deleteModalText.textContent = `Are you sure you want to delete the ENTIRE history of this chat? This action cannot be undone.`;
    } else {
        if(deleteModalText) deleteModalText.textContent = `Are you sure you want to delete this message?`;
    }
    if (deleteModal) deleteModal.style.display = 'block';
};

function closeDeleteModal() {
    if (deleteModal) deleteModal.style.display = 'none';
    messageToDelete = null;
    isClearingChat = false;
};

if (confirmDeleteBtn) {
    confirmDeleteBtn.onclick = function() {
        const deleteImages = deleteImagesCheckbox ? deleteImagesCheckbox.checked : false;
        if (isClearingChat) {
            const formData = new FormData();
            formData.append('character_id', characterId);
            formData.append('delete_images', deleteImages);
            fetch(clearChatUrl, {
                method: 'POST',
                headers: { 'X-CSRFToken': csrfToken, 'X-Requested-With': 'XMLHttpRequest' },
                body: new URLSearchParams(formData)
            }).then(res => res.json()).then(data => {
                if (data.status === 'success') window.location.reload();
            });
        } else if (messageToDelete) {
            const formData = new FormData();
            formData.append('message_id', messageToDelete);
            formData.append('delete_images', deleteImages);
            fetch(deleteMsgUrl, {
                method: 'POST',
                headers: { 'X-CSRFToken': csrfToken, 'X-Requested-With': 'XMLHttpRequest' },
                body: new URLSearchParams(formData)
            }).then(res => res.json()).then(data => {
                if (data.status === 'success') {
                    const msgElement = document.querySelector(`[data-msg-id="${messageToDelete}"]`);
                    if (msgElement) msgElement.remove();
                    closeDeleteModal();
                }
            });
        }
    };
}

function loadUserGallery(type = 'image') {
    const grid = document.getElementById('user-gallery-grid');
    if (!grid) return;

    grid.innerHTML = '<p style="color:gray;">Loading...</p>';

    // Añadir media_type a la URL
    fetch(`${generateUrl}?character_id=${characterId}&media_type=${type}`, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
    .then(res => res.json())
    .then(data => {
        grid.innerHTML = '';

        if (type === 'image') {
            if(data.images && data.images.length > 0) {
                data.images.forEach(url => {
                    const img = document.createElement('img');
                    img.src = url;
                    img.style.width = '100%';
                    img.style.borderRadius = '8px';
                    img.onclick = () => window.open(url);
                    grid.appendChild(img);
                });
            } else {
                grid.innerHTML = '<p style="color:gray;">No images found.</p>';
            }
        } else if (type === 'video') {
            if(data.videos && data.videos.length > 0) {
                data.videos.forEach(url => {
                    const vid = document.createElement('video');
                    vid.src = url;
                    vid.controls = true;
                    vid.style.width = '100%';
                    vid.style.borderRadius = '8px';
                    grid.appendChild(vid);
                });
            } else {
                grid.innerHTML = '<p style="color:gray;">No videos found.</p>';
            }
        }
    });
};

function filterCharacters() {
    const searchInput = document.getElementById('char-search');
    if (searchInput) {
        applyFilters(searchInput.value.toLowerCase());
    }
};

function filterCategory(categoryId, btn) {
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const list = document.getElementById('char-list');
    if (list) {
        list.dataset.activeCategory = categoryId;
        const searchVal = document.getElementById('char-search') ? document.getElementById('char-search').value.toLowerCase() : "";
        applyFilters(searchVal);
    }
};

function toggleSubCategory(subId, btn) {
    btn.classList.toggle('active');
    const list = document.getElementById('char-list');
    if (list) {
        let activeSubs = list.dataset.activeSubs ? list.dataset.activeSubs.split(',').filter(Boolean) : [];
        if (btn.classList.contains('active')) {
            activeSubs.push(subId);
        } else {
            activeSubs = activeSubs.filter(id => id !== subId);
        }
        list.dataset.activeSubs = activeSubs.join(',');
        const searchVal = document.getElementById('char-search') ? document.getElementById('char-search').value.toLowerCase() : "";
        applyFilters(searchVal);
    }
};

function applyFilters(searchVal = "") {
    const list = document.getElementById('char-list');
    if (!list) return;

    const activeCategoryId = list.dataset.activeCategory || 'ALL';
    const activeSubs = list.dataset.activeSubs ? list.dataset.activeSubs.split(',').filter(Boolean) : [];
    const items = document.getElementsByClassName('char-list-item');

    Array.from(items).forEach(item => {
        const name = item.textContent.toLowerCase();
        const itemCategoryId = item.getAttribute('data-category-id');
        const itemSubCategoryId = item.getAttribute('data-subcategory-id');
        const isPrivate = item.getAttribute('data-is-private') === 'true';

        const matchesSearch = name.includes(searchVal);

        let matchesCategory = false;
        if (activeCategoryId === 'PRIVATE') {
            matchesCategory = isPrivate;
        } else if (activeCategoryId === 'ALL') {
            matchesCategory = true;
        } else {
            matchesCategory = itemCategoryId === activeCategoryId;
        }

        const matchesSub = activeSubs.length === 0 || activeSubs.includes(itemSubCategoryId);

        item.style.display = (matchesSearch && matchesCategory && matchesSub) ? 'flex' : 'none';
    });
};

// --- IMAGE VIEWER LOGIC (LIGHTBOX) ---
const viewerModal = document.getElementById('image-viewer');
const viewerImg = document.getElementById('viewer-img');
const viewerContainer = document.getElementById('viewer-container');
const prevArrow = document.querySelector('.nav-arrow.left');
const nextArrow = document.querySelector('.nav-arrow.right');
let currentImageIndex = 0;
let allImages = [];
let zoomLevel = 1;
let isDragging = false;
let startX, startY, translateX = 0, translateY = 0;

function openImageViewer(src) {
    // Recopilar todas las imágenes visibles en el chat
    const images = document.querySelectorAll('.chat-image');
    allImages = Array.from(images).map(img => img.src);
    currentImageIndex = allImages.indexOf(src);

    if (currentImageIndex === -1) return; // Error de seguridad

    updateViewerImage();
    if (viewerModal) viewerModal.style.display = 'flex';
    resetZoom();
};

function closeImageViewer() {
    if (viewerModal) viewerModal.style.display = 'none';
};

function updateViewerImage() {
    if (viewerImg) viewerImg.src = allImages[currentImageIndex];
    resetZoom();
    updateNavButtons();
};

function updateNavButtons() {
    if (!prevArrow || !nextArrow) return;
    // Ocultar flecha izquierda si estamos en el inicio
    if (currentImageIndex <= 0) {
        prevArrow.style.display = 'none';
    } else {
        prevArrow.style.display = 'flex';
    }

    // Ocultar flecha derecha si estamos en el final
    if (currentImageIndex >= allImages.length - 1) {
        nextArrow.style.display = 'none';
    } else {
        nextArrow.style.display = 'flex';
    }
};

function navigateImage(direction) {
    const newIndex = currentImageIndex + direction;
    // Verificar límites estrictos (sin bucle)
    if (newIndex >= 0 && newIndex < allImages.length) {
        currentImageIndex = newIndex;
        updateViewerImage();
    }
};

function resetZoom() {
    zoomLevel = 1;
    translateX = 0;
    translateY = 0;
    updateTransform();
};

function updateTransform() {
    if (viewerImg) viewerImg.style.transform = `translate(${translateX}px, ${translateY}px) scale(${zoomLevel})`;
};

// Zoom con rueda del ratón
if (viewerContainer) {
    viewerContainer.addEventListener('wheel', (e) => {
        e.preventDefault();
        const delta = e.deltaY > 0 ? -0.1 : 0.1;
        zoomLevel = Math.min(Math.max(1, zoomLevel + delta), 4); // Zoom entre 1x y 4x
        updateTransform();
    });

    // Arrastrar imagen (Pan)
    if (viewerImg) {
        viewerImg.addEventListener('mousedown', (e) => {
            if (zoomLevel > 1) {
                isDragging = true;
                startX = e.clientX - translateX;
                startY = e.clientY - translateY;
                viewerImg.style.cursor = 'grabbing';
            }
        });
    }

    // --- SOPORTE TÁCTIL (SWIPE Y PINCH ZOOM) ---
    let touchStartX = 0;
    let touchEndX = 0;
    let initialDistance = 0;

    viewerContainer.addEventListener('touchstart', (e) => {
        if (e.touches.length === 1) {
            touchStartX = e.changedTouches[0].screenX;
            if (zoomLevel > 1) {
                isDragging = true;
                startX = e.touches[0].clientX - translateX;
                startY = e.touches[0].clientY - translateY;
            }
        } else if (e.touches.length === 2) {
            initialDistance = Math.hypot(
                e.touches[0].pageX - e.touches[1].pageX,
                e.touches[0].pageY - e.touches[1].pageY
            );
        }
    });

    viewerContainer.addEventListener('touchmove', (e) => {
        e.preventDefault(); // Prevenir scroll de página
        if (e.touches.length === 1 && zoomLevel > 1 && isDragging) {
            translateX = e.touches[0].clientX - startX;
            translateY = e.touches[0].clientY - startY;
            updateTransform();
        } else if (e.touches.length === 2) {
            const currentDistance = Math.hypot(
                e.touches[0].pageX - e.touches[1].pageX,
                e.touches[0].pageY - e.touches[1].pageY
            );
            const delta = currentDistance / initialDistance;
            zoomLevel = Math.min(Math.max(1, zoomLevel * delta), 4);
            initialDistance = currentDistance; // Actualizar para suavidad
            updateTransform();
        }
    });

    viewerContainer.addEventListener('touchend', (e) => {
        if (e.changedTouches.length === 1 && zoomLevel === 1) {
            touchEndX = e.changedTouches[0].screenX;
            handleSwipe();
        }
        isDragging = false;
    });

    function handleSwipe() {
        const threshold = 50;
        // Swipe Izquierda -> Siguiente (solo si no es el último)
        if (touchEndX < touchStartX - threshold) {
            if (currentImageIndex < allImages.length - 1) navigateImage(1);
        }
        // Swipe Derecha -> Anterior (solo si no es el primero)
        if (touchEndX > touchStartX + threshold) {
            if (currentImageIndex > 0) navigateImage(-1);
        }
    }
}

window.addEventListener('mouseup', () => {
    isDragging = false;
    if(viewerImg) viewerImg.style.cursor = 'grab';
});

window.addEventListener('mousemove', (e) => {
    if (!isDragging) return;
    e.preventDefault();
    translateX = e.clientX - startX;
    translateY = e.clientY - startY;
    updateTransform();
});

// Teclas de flecha
document.addEventListener('keydown', (e) => {
    if (viewerModal && viewerModal.style.display === 'flex') {
        if (e.key === 'ArrowLeft') navigateImage(-1);
        if (e.key === 'ArrowRight') navigateImage(1);
        if (e.key === 'Escape') closeImageViewer();
    }
    // NUEVO: Teclas para video
    if (typeof videoViewerModal !== 'undefined' && videoViewerModal && videoViewerModal.style.display === 'flex') {
        if (e.key === 'ArrowLeft') navigateVideo(-1);
        if (e.key === 'ArrowRight') navigateVideo(1);
        if (e.key === 'Escape') closeVideoViewer();
    }
});

// --- VIDEO VIEWER LOGIC (NUEVO) ---
const videoViewerModal = document.getElementById('video-viewer');
const viewerVideo = document.getElementById('viewer-video');
const videoPrevArrow = videoViewerModal ? videoViewerModal.querySelector('.nav-arrow.left') : null;
const videoNextArrow = videoViewerModal ? videoViewerModal.querySelector('.nav-arrow.right') : null;
let currentVideoIndex = 0;
let allVideos = [];

function openVideoViewer(src) {
    // Recopilar todos los videos visibles en el chat
    const videos = document.querySelectorAll('.chat-video');
    allVideos = Array.from(videos).map(vid => vid.src);
    currentVideoIndex = allVideos.indexOf(src);

    if (currentVideoIndex === -1) return;

    updateViewerVideo();
    if (videoViewerModal) videoViewerModal.style.display = 'flex';
}

function closeVideoViewer() {
    if (videoViewerModal) videoViewerModal.style.display = 'none';
    if (viewerVideo) viewerVideo.pause(); // Detener reproducción al cerrar
}

function updateViewerVideo() {
    if (viewerVideo) {
        viewerVideo.src = allVideos[currentVideoIndex];
        viewerVideo.play(); // Auto-play al cambiar
    }
    updateVideoNavButtons();
}

function updateVideoNavButtons() {
    if (!videoPrevArrow || !videoNextArrow) return;

    if (currentVideoIndex <= 0) {
        videoPrevArrow.style.display = 'none';
    } else {
        videoPrevArrow.style.display = 'flex';
    }

    if (currentVideoIndex >= allVideos.length - 1) {
        videoNextArrow.style.display = 'none';
    } else {
        videoNextArrow.style.display = 'flex';
    }
}

function navigateVideo(direction) {
    const newIndex = currentVideoIndex + direction;
    if (newIndex >= 0 && newIndex < allVideos.length) {
        currentVideoIndex = newIndex;
        updateViewerVideo();
    }
}

// --- SHOWCASE EXAMPLES LOGIC ---
document.addEventListener('DOMContentLoaded', () => {
    const carousel = document.querySelector('.example-carousel');
    if (!carousel) return;

    const track = carousel.querySelector('.carousel-track');
    const slides = Array.from(track.children);
    const nextButton = carousel.querySelector('.next');
    const prevButton = carousel.querySelector('.prev');

    if (slides.length <= 1) {
        if(nextButton) nextButton.style.display = 'none';
        if(prevButton) prevButton.style.display = 'none';
        return;
    };

    let currentIndex = 0;
    let autoSlideInterval;

    const updateCarousel = () => {
        slides.forEach((slide, index) => {
            slide.style.display = index === currentIndex ? 'block' : 'none';
        });
    };

    const startAutoSlide = () => {
        clearInterval(autoSlideInterval);
        autoSlideInterval = setInterval(() => {
            currentIndex = (currentIndex + 1) % slides.length;
            updateCarousel();
        }, 2000);
    };

    const resetAutoSlide = () => {
        startAutoSlide();
    };

    if(nextButton) {
        nextButton.addEventListener('click', () => {
            currentIndex = (currentIndex + 1) % slides.length;
            updateCarousel();
            resetAutoSlide();
        });
    }

    if(prevButton) {
        prevButton.addEventListener('click', () => {
            currentIndex = (currentIndex - 1 + slides.length) % slides.length;
            updateCarousel();
            resetAutoSlide();
        });
    }

    // Add click listener to copy prompt
    slides.forEach(slide => {
        slide.addEventListener('click', () => {
            const promptText = slide.dataset.prompt;
            const promptInput = document.getElementById('prompt-input');
            if (promptInput) {
                promptInput.value = promptText;
                promptInput.dispatchEvent(new Event('input', { bubbles: true }));
                showToast('Prompt copied!', 'success');
            }
        });
    });

    updateCarousel(); // Initial setup
    startAutoSlide(); // Start automatic sliding
});

// --- ANIMATION LOGIC ---
function createAnimatedShapes() {
    const container = document.querySelector('.main-content');
    if (!container) return;

    let animationContainer = document.querySelector('.animation-container');
    if (!animationContainer) {
        animationContainer = document.createElement('div');
        animationContainer.className = 'animation-container';
        container.prepend(animationContainer); // Añadir al principio para que esté detrás
    }

    const shapes = ['heart', 'bubble'];
    const shapeCount = 25; // Aumentamos un poco la cantidad

    // Limpiar formas existentes para evitar duplicados
    animationContainer.innerHTML = '';

    for (let i = 0; i < shapeCount; i++) {
        const shapeType = shapes[Math.floor(Math.random() * shapes.length)];
        const shape = document.createElement('div');
        shape.classList.add('shape', shapeType);

        // Establecer variables CSS para el estado inicial y la animación
        shape.style.setProperty('--start-x', `${Math.random() * 100}%`);
        shape.style.setProperty('--start-y', `${Math.random() * 100}%`);
        shape.style.setProperty('--anim-duration', `${Math.random() * 12 + 18}s`); // 18-30s
        shape.style.setProperty('--anim-delay', `${Math.random() * 1}s`); // 0-1s de retraso
        shape.style.setProperty('--drift-x', `${Math.random() * 300 - 150}px`); // Deriva horizontal más amplia
        shape.style.setProperty('--drift-y', `${Math.random() * 150 + 100}px`); // Variación de deriva vertical

        if (shapeType === 'heart') {
             shape.innerHTML = '❤';
             shape.style.fontSize = `${Math.random() * 20 + 12}px`; // Un poco más grandes
        } else {
             shape.style.width = `${Math.random() * 25 + 8}px`; // Burbujas más grandes
             shape.style.height = shape.style.width;
        }

        animationContainer.appendChild(shape);
    }
}

// --- NUEVA FUNCIÓN PARA SELECCIONAR OPCIONES ---
function selectSingleOption(btn, inputId, value) {
    const input = document.getElementById(inputId);
    if (input) {
        input.value = value;
    }

    const container = btn.closest('.output-options-grid');
    if (container) {
        container.querySelectorAll('.output-option-btn').forEach(b => b.classList.remove('active'));
    }
    btn.classList.add('active');

    // Cerrar el menú
    const menu = btn.closest('.settings-dropdown-menu');
    const dropdownBtn = document.querySelector(`[onclick*="${menu.id}"]`);
    if (menu) menu.classList.remove('show');
    if (dropdownBtn) dropdownBtn.classList.remove('active');
}