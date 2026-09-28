// Copyright 2026 Yves Vallée
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * Rotation fine et recadrage manuel du justificatif, avant de relancer
 * l'analyse sur l'image corrigée.
 *
 * Tout se fait dans le navigateur, sur un canevas : le serveur ne reçoit
 * que le résultat, déjà en JPEG (action_expense_scan_retouch). Rien n'est
 * envoyé tant que l'utilisateur n'a pas validé.
 *
 * Deux temps, comme un vrai scanner : d'abord la rotation, sur l'image
 * entière (pivotée, jamais rognée par la rotation elle-même) ; le
 * recadrage se règle ensuite, sur cette image déjà droite. Changer la
 * rotation réinitialise donc le recadrage — le réconcilier avec un angle
 * différent n'aurait pas de sens.
 */
import { _t } from "@web/core/l10n/translation";
import { Component, onWillUnmount, onMounted, useRef, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { browser } from "@web/core/browser/browser";
import { useDebounced } from "@web/core/utils/timing";

//: Distance, en pixels du canevas, en dessous de laquelle un appui vise
//: une poignée plutôt que déplace le cadre entier.
const HANDLE_HIT_RADIUS = 22;
//: Longueur de chaque branche des poignées en équerre.
const HANDLE_LENGTH = 18;
//: Taille minimale du recadrage, en pixels du canevas : sous ce seuil, le
//: résultat n'aurait plus de sens (une poignée qui en croise une autre).
const MIN_CROP_SIZE = 24;
//: Rotation fine, en degrés de part et d'autre du quart de tour choisi.
const FINE_RANGE = 45;
//: Plafond de l'image retouchée, en pixels : sous la limite de canevas des
//: navigateurs mobiles (16,7 Mpx sur iOS), et bien au-delà de ce que lit
//: le moteur, qui ramène le ticket à 1 800 px de côté.
const MAX_OUTPUT_PIXELS = 12000000;

function normalizeQuarter(quarter) {
    return ((quarter % 4) + 4) % 4;
}

/**
 * Pièce jointe principale d'une dépense, si c'est une image retouchable.
 *
 * `message_main_attachment_id` arrive tantôt en tuple `[id, nom]`, tantôt
 * en objet `{id, display_name}` selon la version du modèle relationnel.
 * Le canevas ne sait pas dessiner un PDF : on l'écarte à son extension,
 * sans requête de plus.
 *
 * @returns {number|null} l'identifiant de la pièce, ou null
 */
export function retouchableAttachmentId(record) {
    const value = record.data.message_main_attachment_id;
    if (!value) {
        return null;
    }
    const [id, name] = Array.isArray(value) ? value : [value.id, value.display_name];
    return (name || "").toLowerCase().endsWith(".pdf") ? null : id;
}

/**
 * Ouvre la retouche, puis corrige le justificatif et relance l'analyse.
 *
 * @param {{dialog: Object, orm: Object}} services
 * @param {Object} record la dépense, telle que le formulaire la tient
 */
export function openRetouchDialog({ dialog, orm }, record) {
    dialog.add(RetouchDialog, {
        attachmentId: retouchableAttachmentId(record),
        apply: async (base64) => {
            // Une saisie en cours partirait sinon avec le rechargement. Si
            // elle ne peut pas s'enregistrer (champ obligatoire vide), on
            // s'arrête là : le formulaire signale lui-même ce qui manque.
            if (!(await record.save())) {
                return false;
            }
            await orm.call("hr.expense", "action_expense_scan_retouch", [[record.resId], base64]);
            await orm.call("hr.expense", "action_expense_scan_rescan", [[record.resId]]);
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
        this.drag = null; // { handle } | { move, startX, startY, crop0 } pendant un geste

        this.onResize = useDebounced(() => this.layout(), 200);

        onMounted(() => {
            browser.addEventListener("resize", this.onResize);
            const image = new Image();
            image.onload = () => {
                this.image = image;
                this.state.loaded = true;
                // Le canevas est dans le DOM dès l'ouverture (masqué tant
                // que l'image charge) : on peut le dimensionner tout de suite.
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

    /** Rotation totale, quart de tour et réglage fin combinés. */
    get angleDegrees() {
        return this.state.quarter * 90 + this.state.fine;
    }

    /**
     * Dimensions du rectangle qui contient l'image pivotée, pour une image
     * de ``width`` × ``height`` : la rotation ne rogne jamais rien.
     */
    rotatedBounds(width, height) {
        const angle = (this.angleDegrees * Math.PI) / 180;
        const cos = Math.abs(Math.cos(angle));
        const sin = Math.abs(Math.sin(angle));
        return { width: width * cos + height * sin, height: width * sin + height * cos };
    }

    // ------------------------------------------------------------------
    // Aperçu
    // ------------------------------------------------------------------

    /** Échelle de l'aperçu, d'après la place disponible, puis cadre entier. */
    layout() {
        if (!this.image) {
            return;
        }
        const container = this.containerRef.el;
        const maxWidth = container ? container.clientWidth : 480;
        // Un peu de marge : à un angle intermédiaire, le rectangle qui
        // contient l'image pivotée est plus grand que l'image elle-même.
        const width = Math.max(240, maxWidth) * 0.86;
        const height = Math.min(browser.innerHeight * 0.5, 480);
        this.scale = Math.min(
            width / this.image.naturalWidth, height / this.image.naturalHeight, 1);
        this.resetCrop();
    }

    /** Le cadre couvre toute l'image : point de départ, et bouton « tout ». */
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

    /** Dessine l'image pivotée, puis le cadre par-dessus. */
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
        // Quatre bandes sombres autour du cadre : ce qui sera retiré.
        ctx.fillStyle = "rgba(0, 0, 0, 0.5)";
        ctx.fillRect(0, 0, width, crop.y0);
        ctx.fillRect(0, crop.y1, width, height - crop.y1);
        ctx.fillRect(0, crop.y0, crop.x0, crop.y1 - crop.y0);
        ctx.fillRect(crop.x1, crop.y0, width - crop.x1, crop.y1 - crop.y0);
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 2;
        ctx.strokeRect(crop.x0, crop.y0, crop.x1 - crop.x0, crop.y1 - crop.y0);
        // Poignées en équerre, tournées vers l'intérieur du cadre : elles
        // restent entières quand le cadre touche le bord de l'image.
        const directions = [[1, 1], [-1, 1], [1, -1], [-1, -1]];
        ctx.lineWidth = 5;
        ctx.lineCap = "square";
        this.handlePositions(crop).forEach(([x, y], index) => {
            const [dx, dy] = directions[index];
            ctx.beginPath();
            ctx.moveTo(x + dx * HANDLE_LENGTH, y + dy * 2.5);
            ctx.lineTo(x + dx * 2.5, y + dy * 2.5);
            ctx.lineTo(x + dx * 2.5, y + dy * HANDLE_LENGTH);
            ctx.stroke();
        });
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

    // ------------------------------------------------------------------
    // Recadrage : souris ou doigt, par les événements pointeur
    // ------------------------------------------------------------------

    /** Position d'un pointeur, en pixels du canevas (affiché plus petit ou non). */
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
            // Chaque coin ne bouge que ses deux bords, sans jamais croiser
            // les bords opposés.
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
     * Version définitive, encodée en JPEG (base64, sans l'en-tête).
     *
     * Composée directement dans le cadre final : l'image entière pivotée
     * n'existe jamais en mémoire à pleine taille. Une photo de 50 Mpx,
     * pivotée, dépasserait sinon la taille de canevas que tolère un
     * téléphone. Le résultat est aussi plafonné (MAX_OUTPUT_PIXELS).
     */
    compose() {
        const iw = this.image.naturalWidth;
        const ih = this.image.naturalHeight;
        const bounds = this.rotatedBounds(iw, ih);
        // Le cadre est mesuré sur l'aperçu : un même rapport le reporte
        // à pleine résolution, dans les deux dimensions.
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
        // Coins découverts par la rotation : blancs, comme le papier,
        // plutôt que noirs — un bord sombre se lirait comme un trait.
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
