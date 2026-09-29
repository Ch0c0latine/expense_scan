// Copyright 2026 Yves Vallée
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * Retouche du justificatif : rotation et recadrage.
 *
 * La retouche part toujours de la photo d'origine. L'image est transformée
 * dans le navigateur, sur un canevas ; le serveur reçoit le résultat en
 * JPEG (action_expense_scan_retouch), qui remplace l'image affichée sans
 * relancer l'analyse.
 *
 * La rotation s'applique à l'image entière, sans la rogner. Le cadre de
 * recadrage est défini sur l'image pivotée : changer la rotation le
 * réinitialise.
 */
import { _t } from "@web/core/l10n/translation";
import { Component, onWillUnmount, onMounted, useRef, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { browser } from "@web/core/browser/browser";
import { useDebounced } from "@web/core/utils/timing";

//: Distance, en pixels du canevas, en deçà de laquelle un appui saisit une
//: poignée ; au-delà, un appui dans le cadre le déplace.
const HANDLE_HIT_RADIUS = 22;
//: Longueur de chaque branche des poignées en équerre.
const HANDLE_LENGTH = 18;
//: Taille minimale du cadre, en pixels du canevas.
const MIN_CROP_SIZE = 24;
//: Rotation fine, en degrés de part et d'autre du quart de tour choisi.
const FINE_RANGE = 45;
//: Taille maximale de l'image produite, en pixels. Reste sous la limite de
//: canevas de Safari iOS (16,7 Mpx) ; le moteur OCR lit à 1 800 px de côté.
const MAX_OUTPUT_PIXELS = 12000000;

function normalizeQuarter(quarter) {
    return ((quarter % 4) + 4) % 4;
}

/**
 * Pièce jointe d'un champ many2one : tuple `[id, nom]` ou objet
 * `{id, display_name}` selon la version du modèle relationnel.
 */
function attachmentOf(value) {
    if (!value) {
        return null;
    }
    const [id, name] = Array.isArray(value) ? value : [value.id, value.display_name];
    return { id, isImage: !(name || "").toLowerCase().endsWith(".pdf") };
}

/**
 * Image de départ de la retouche : la photo d'origine, ou l'image affichée
 * si l'origine est un PDF (le canevas n'affiche pas les PDF).
 *
 * @returns {number|null} l'identifiant de la pièce, ou null si rien n'est
 * retouchable
 */
export function retouchSourceId(record) {
    const main = attachmentOf(record.data.message_main_attachment_id);
    if (!main) {
        return null;
    }
    const original = attachmentOf(record.data.scan_original_attachment_id);
    if (original?.isImage) {
        return original.id;
    }
    return main.isImage ? main.id : null;
}

/**
 * Ouvre la retouche ; à la validation, remplace l'image affichée.
 *
 * @param {{dialog: Object, orm: Object}} services
 * @param {Object} record enregistrement de la dépense dans le formulaire
 */
export function openRetouchDialog({ dialog, orm }, record) {
    dialog.add(RetouchDialog, {
        attachmentId: retouchSourceId(record),
        apply: async (base64) => {
            // Enregistre d'abord les saisies en cours, que le rechargement
            // effacerait. En cas d'échec, le formulaire affiche l'erreur.
            if (!(await record.save())) {
                return false;
            }
            await orm.call("hr.expense", "action_expense_scan_retouch", [[record.resId], base64]);
            await record.model.load();
            return true;
        },
    });
}

export class RetouchDialog extends Component {
    static template = "expense_scan.RetouchDialog";
    static components = { Dialog };
    static props = {
        attachmentId: Number,
        apply: Function,
        close: Function,
    };

    setup() {
        this.canvasRef = useRef("canvas");
        this.containerRef = useRef("container");
        this.state = useState({
            quarter: 0,
            fine: 0,
            crop: null,
            loaded: false,
            busy: false,
            error: null,
            dragging: false,
        });
        this.image = null;
        this.scale = 1;
        this.drag = null; // { handle } ou { move, startX, startY, crop0 }

        this.onResize = useDebounced(() => this.layout(), 200);

        onMounted(() => {
            browser.addEventListener("resize", this.onResize);
            const image = new Image();
            image.onload = () => {
                this.image = image;
                this.state.loaded = true;
                this.layout();
            };
            image.onerror = () => {
                this.state.error = _t("Cette pièce ne peut pas être ouverte comme une image.");
            };
            image.src = `/web/image/${this.props.attachmentId}`;
        });
        onWillUnmount(() => browser.removeEventListener("resize", this.onResize));
    }

    get title() {
        return _t("Retouche");
    }

    get fineRange() {
        return FINE_RANGE;
    }

    /** Rotation totale, en degrés. */
    get angleDegrees() {
        return this.state.quarter * 90 + this.state.fine;
    }

    /** Rectangle englobant une image ``width`` × ``height`` après rotation. */
    rotatedBounds(width, height) {
        const angle = (this.angleDegrees * Math.PI) / 180;
        const cos = Math.abs(Math.cos(angle));
        const sin = Math.abs(Math.sin(angle));
        return { width: width * cos + height * sin, height: width * sin + height * cos };
    }

    // ------------------------------------------------------------------
    // Aperçu
    // ------------------------------------------------------------------

    /** Calcule l'échelle de l'aperçu d'après la place disponible. */
    layout() {
        if (!this.image) {
            return;
        }
        const container = this.containerRef.el;
        const maxWidth = container ? container.clientWidth : 480;
        // Marge pour le rectangle englobant, plus grand que l'image pivotée.
        const width = Math.max(240, maxWidth) * 0.86;
        const height = Math.min(browser.innerHeight * 0.6, 560);
        this.scale = Math.min(
            width / this.image.naturalWidth, height / this.image.naturalHeight, 1);
        this.resetCrop();
    }

    /** Remet le cadre sur toute l'image. */
    resetCrop() {
        const canvas = this.canvasRef.el;
        if (!canvas || !this.image) {
            return;
        }
        this.sizeCanvas();
        this.state.crop = { x0: 0, y0: 0, x1: canvas.width, y1: canvas.height };
        this.draw();
    }

    sizeCanvas() {
        const canvas = this.canvasRef.el;
        const bounds = this.rotatedBounds(
            this.image.naturalWidth * this.scale, this.image.naturalHeight * this.scale);
        canvas.width = Math.round(bounds.width);
        canvas.height = Math.round(bounds.height);
    }

    /** Dessine l'image pivotée et le cadre. */
    draw() {
        const canvas = this.canvasRef.el;
        if (!canvas || !this.image) {
            return;
        }
        const angle = (this.angleDegrees * Math.PI) / 180;
        const w = this.image.naturalWidth * this.scale;
        const h = this.image.naturalHeight * this.scale;
        const ctx = canvas.getContext("2d");
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.save();
        ctx.translate(canvas.width / 2, canvas.height / 2);
        ctx.rotate(angle);
        ctx.drawImage(this.image, -w / 2, -h / 2, w, h);
        ctx.restore();
        this.drawCropOverlay(ctx, canvas.width, canvas.height);
    }

    drawCropOverlay(ctx, width, height) {
        const crop = this.state.crop;
        if (!crop) {
            return;
        }
        ctx.save();
        // Assombrit la zone hors du cadre.
        ctx.fillStyle = "rgba(0, 0, 0, 0.5)";
        ctx.fillRect(0, 0, width, crop.y0);
        ctx.fillRect(0, crop.y1, width, height - crop.y1);
        ctx.fillRect(0, crop.y0, crop.x0, crop.y1 - crop.y0);
        ctx.fillRect(crop.x1, crop.y0, width - crop.x1, crop.y1 - crop.y0);
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 2;
        ctx.strokeRect(crop.x0, crop.y0, crop.x1 - crop.x0, crop.y1 - crop.y0);
        // Poignées en équerre tracées vers l'intérieur du cadre, pour rester
        // visibles au bord du canevas ; contour sombre sous un trait blanc.
        const directions = [[1, 1], [-1, 1], [1, -1], [-1, -1]];
        ctx.lineCap = "square";
        for (const [color, width] of [["rgba(0, 0, 0, 0.6)", 7], ["#ffffff", 4]]) {
            ctx.strokeStyle = color;
            ctx.lineWidth = width;
            this.handlePositions(crop).forEach(([x, y], index) => {
                const [dx, dy] = directions[index];
                ctx.beginPath();
                ctx.moveTo(x + dx * HANDLE_LENGTH, y + dy * 3.5);
                ctx.lineTo(x + dx * 3.5, y + dy * 3.5);
                ctx.lineTo(x + dx * 3.5, y + dy * HANDLE_LENGTH);
                ctx.stroke();
            });
        }
        ctx.restore();
    }

    handlePositions(crop) {
        return [
            [crop.x0, crop.y0], [crop.x1, crop.y0],
            [crop.x0, crop.y1], [crop.x1, crop.y1],
        ];
    }

    // ------------------------------------------------------------------
    // Rotation
    // ------------------------------------------------------------------

    turn(step) {
        this.state.quarter = normalizeQuarter(this.state.quarter + step);
        this.resetCrop();
    }

    onFineInput(event) {
        this.state.fine = Number(event.target.value);
        this.resetCrop();
    }

    resetFine() {
        this.state.fine = 0;
        this.resetCrop();
    }

    // ------------------------------------------------------------------
    // Recadrage (événements pointeur : souris et tactile)
    // ------------------------------------------------------------------

    /** Position du pointeur en pixels du canevas. */
    canvasPoint(event) {
        const canvas = this.canvasRef.el;
        const rect = canvas.getBoundingClientRect();
        return {
            x: ((event.clientX - rect.left) / rect.width) * canvas.width,
            y: ((event.clientY - rect.top) / rect.height) * canvas.height,
        };
    }

    nearestHandle(point, crop) {
        const names = ["x0y0", "x1y0", "x0y1", "x1y1"];
        let best = null;
        let bestDistance = HANDLE_HIT_RADIUS;
        this.handlePositions(crop).forEach(([x, y], index) => {
            const distance = Math.hypot(point.x - x, point.y - y);
            if (distance <= bestDistance) {
                bestDistance = distance;
                best = names[index];
            }
        });
        return best;
    }

    onPointerDown(event) {
        const crop = this.state.crop;
        if (!crop || this.state.busy) {
            return;
        }
        const point = this.canvasPoint(event);
        const handle = this.nearestHandle(point, crop);
        if (handle) {
            this.drag = { handle };
        } else if (point.x > crop.x0 && point.x < crop.x1
                   && point.y > crop.y0 && point.y < crop.y1) {
            this.drag = { move: true, startX: point.x, startY: point.y, crop0: { ...crop } };
        } else {
            return;
        }
        this.state.dragging = true;
        this.canvasRef.el.setPointerCapture(event.pointerId);
        event.preventDefault();
    }

    onPointerMove(event) {
        if (!this.drag) {
            return;
        }
        const canvas = this.canvasRef.el;
        const point = this.canvasPoint(event);
        const crop = this.state.crop;
        const clamp = (value, max) => Math.min(Math.max(value, 0), max);
        if (this.drag.handle) {
            const x = clamp(point.x, canvas.width);
            const y = clamp(point.y, canvas.height);
            const next = { ...crop };
            // Le coin déplace ses deux bords, sans dépasser les bords opposés.
            if (this.drag.handle.startsWith("x0")) {
                next.x0 = Math.min(x, crop.x1 - MIN_CROP_SIZE);
            } else {
                next.x1 = Math.max(x, crop.x0 + MIN_CROP_SIZE);
            }
            if (this.drag.handle.endsWith("y0")) {
                next.y0 = Math.min(y, crop.y1 - MIN_CROP_SIZE);
            } else {
                next.y1 = Math.max(y, crop.y0 + MIN_CROP_SIZE);
            }
            this.state.crop = next;
        } else {
            const start = this.drag.crop0;
            const width = start.x1 - start.x0;
            const height = start.y1 - start.y0;
            const x0 = clamp(start.x0 + point.x - this.drag.startX, canvas.width - width);
            const y0 = clamp(start.y0 + point.y - this.drag.startY, canvas.height - height);
            this.state.crop = { x0, y0, x1: x0 + width, y1: y0 + height };
        }
        this.draw();
    }

    onPointerUp(event) {
        this.drag = null;
        this.state.dragging = false;
        const canvas = this.canvasRef.el;
        if (canvas && canvas.hasPointerCapture(event.pointerId)) {
            canvas.releasePointerCapture(event.pointerId);
        }
    }

    // ------------------------------------------------------------------
    // Validation
    // ------------------------------------------------------------------

    async onApply() {
        this.state.busy = true;
        this.state.error = null;
        try {
            if (await this.props.apply(this.compose())) {
                this.props.close();
                return;
            }
            this.state.error = _t(
                "La fiche ne peut pas être enregistrée en l'état : corrigez-la, puis réessayez.");
            this.state.busy = false;
        } catch (error) {
            this.state.error = _t("La retouche n'a pas pu être enregistrée. Réessayez.");
            this.state.busy = false;
            throw error;
        }
    }

    /**
     * Image finale en JPEG, encodée en base64 sans l'en-tête.
     *
     * Dessinée directement dans un canevas à la taille du cadre, sans
     * canevas intermédiaire pour l'image pivotée entière : pour une grande
     * photo, celui-ci dépasserait la limite des navigateurs mobiles.
     * Taille plafonnée à MAX_OUTPUT_PIXELS.
     */
    compose() {
        const iw = this.image.naturalWidth;
        const ih = this.image.naturalHeight;
        const bounds = this.rotatedBounds(iw, ih);
        // Passage des coordonnées de l'aperçu à la pleine résolution.
        const ratio = bounds.width / this.canvasRef.el.width;
        const crop = this.state.crop;
        const cropX = crop.x0 * ratio;
        const cropY = crop.y0 * ratio;
        const cropWidth = (crop.x1 - crop.x0) * ratio;
        const cropHeight = (crop.y1 - crop.y0) * ratio;
        const shrink = Math.min(1, Math.sqrt(MAX_OUTPUT_PIXELS / (cropWidth * cropHeight)));

        const output = document.createElement("canvas");
        output.width = Math.max(1, Math.round(cropWidth * shrink));
        output.height = Math.max(1, Math.round(cropHeight * shrink));
        const ctx = output.getContext("2d");
        // Fond blanc pour les coins découverts par la rotation : un fond
        // noir serait lu comme du texte.
        ctx.fillStyle = "#ffffff";
        ctx.fillRect(0, 0, output.width, output.height);
        ctx.scale(shrink, shrink);
        ctx.translate(-cropX, -cropY);
        ctx.translate(bounds.width / 2, bounds.height / 2);
        ctx.rotate((this.angleDegrees * Math.PI) / 180);
        ctx.drawImage(this.image, -iw / 2, -ih / 2, iw, ih);
        return output.toDataURL("image/jpeg", 0.9).split(",")[1];
    }
}
