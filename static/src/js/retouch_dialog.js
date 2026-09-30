// Copyright 2026 Yves Vallée
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * Receipt retouch: rotation and crop.
 *
 * The retouch always starts from the uploaded image (original photo, or the
 * first page of a PDF rendered by the server), on which the editor applies
 * the settings of the previous retouch again. The image is transformed in
 * the browser, on a canvas; the server receives the result as JPEG
 * (action_expense_scan_retouch), which replaces the displayed image without
 * scanning again.
 *
 * The rotation applies to the whole image, without cutting it. The crop
 * frame is set on the rotated image: changing the rotation resets it to the
 * whole image.
 */
import { _t } from "@web/core/l10n/translation";
import { Component, onWillUnmount, onMounted, useRef, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { browser } from "@web/core/browser/browser";
import { useService } from "@web/core/utils/hooks";
import { useDebounced } from "@web/core/utils/timing";

//: Distance, in canvas pixels, below which a press grabs a handle; beyond
//: it, a press inside the frame moves it.
const HANDLE_HIT_RADIUS = 22;
//: Length of each arm of the corner handles.
const HANDLE_LENGTH = 18;
//: Minimum frame size, in canvas pixels.
const MIN_CROP_SIZE = 24;
//: Fine rotation, in degrees on either side of the chosen quarter turn.
const FINE_RANGE = 45;
//: Maximum size of the image produced, in pixels. Stays under the canvas
//: limit of Safari on iOS (16.7 Mpx); the OCR engine reads at 1,800 px.
const MAX_OUTPUT_PIXELS = 12000000;

function normalizeQuarter(quarter) {
    return ((quarter % 4) + 4) % 4;
}

/** True if the expense has a receipt (image or PDF) to retouch. */
export function hasReceipt(record) {
    return Boolean(record.resId && record.data.message_main_attachment_id);
}

/**
 * Open the retouch; once applied, replace the displayed image.
 *
 * The services are taken from ``env.services`` and not through
 * ``useService``: the save that precedes the retouch may recreate the
 * component that opened the dialog (preview panel of a PDF), and a call made
 * through a destroyed component fails ("Component is destroyed").
 *
 * @param {Object} env environment of the component opening the retouch
 * @param {Object} record record of the expense in the form
 */
export function openRetouchDialog(env, record) {
    const { dialog, orm } = env.services;
    dialog.add(RetouchDialog, {
        resId: record.resId,
        apply: async (base64, params) => {
            // Save the pending input first, which the reload would wipe. On
            // failure, the form shows the error.
            if (!(await record.save())) {
                return false;
            }
            await orm.call(
                "hr.expense", "action_expense_scan_retouch", [[record.resId], base64, params]);
            await record.model.load();
            return true;
        },
    });
}

export class RetouchDialog extends Component {
    static template = "expense_scan.RetouchDialog";
    static components = { Dialog };
    static props = {
        resId: Number,
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
            auto: false,
            error: null,
            dragging: false,
            // Height of the image area, fixed for the whole retouch: the
            // controls below must not move under the finger when the image
            // turns.
            stageHeight: 0,
        });
        this.orm = useService("orm");
        this.image = null;
        this.scale = 1;
        this.drag = null; // { handle } or { move, startX, startY, crop0 }

        this.onResize = useDebounced(() => this.layout(this.currentParams()), 200);

        onMounted(async () => {
            browser.addEventListener("resize", this.onResize);
            const { url, params } = await this.orm.call(
                "hr.expense", "expense_scan_retouch_data", [[this.props.resId]]);
            const image = new Image();
            image.onload = () => {
                this.image = image;
                this.state.loaded = true;
                this.layout(params);
            };
            image.onerror = () => {
                this.state.error = _t("This attachment cannot be opened as an image.");
            };
            image.src = url;
        });
        onWillUnmount(() => browser.removeEventListener("resize", this.onResize));
    }

    get title() {
        return _t("Retouch");
    }

    get fineRange() {
        return FINE_RANGE;
    }

    /** Total rotation, in degrees. */
    get angleDegrees() {
        return this.state.quarter * 90 + this.state.fine;
    }

    /** Bounding rectangle of a ``width`` × ``height`` image after rotation. */
    rotatedBounds(width, height) {
        const angle = (this.angleDegrees * Math.PI) / 180;
        const cos = Math.abs(Math.cos(angle));
        const sin = Math.abs(Math.sin(angle));
        return { width: width * cos + height * sin, height: width * sin + height * cos };
    }

    // ------------------------------------------------------------------
    // Preview
    // ------------------------------------------------------------------

    /** Compute the preview scale from the space available, then apply ``params``. */
    layout(params) {
        if (!this.image) {
            return;
        }
        const container = this.containerRef.el;
        const maxWidth = container ? container.clientWidth : 480;
        // Margin for the bounding rectangle, larger than the rotated image.
        const width = Math.max(240, maxWidth) * 0.86;
        const height = Math.min(browser.innerHeight * 0.6, 560);
        this.scale = Math.min(
            width / this.image.naturalWidth, height / this.image.naturalHeight, 1);
        // Tallest the image can get: upright, turned a quarter, or at 45°.
        const w = this.image.naturalWidth * this.scale;
        const h = this.image.naturalHeight * this.scale;
        this.state.stageHeight = Math.ceil(Math.min(height, Math.max(w, h, (w + h) / Math.SQRT2)));
        this.setParams(params);
    }

    /**
     * Apply settings: ``{quarter, fine, crop}``, the frame as fractions of
     * the rotated image. Without settings: the uploaded image, untouched.
     */
    setParams(params) {
        this.state.quarter = normalizeQuarter(params?.quarter || 0);
        this.state.fine = Math.min(Math.max(params?.fine || 0, -FINE_RANGE), FINE_RANGE);
        const canvas = this.canvasRef.el;
        if (!canvas || !this.image) {
            return;
        }
        this.sizeCanvas();
        const [x0, y0, x1, y1] = params?.crop || [0, 0, 1, 1];
        this.state.crop = {
            x0: x0 * canvas.width, y0: y0 * canvas.height,
            x1: x1 * canvas.width, y1: y1 * canvas.height,
        };
        this.draw();
    }

    /** Current settings, in the ``setParams`` format. */
    currentParams() {
        const canvas = this.canvasRef.el;
        const crop = this.state.crop;
        if (!canvas || !crop || !canvas.width || !canvas.height) {
            return null;
        }
        return {
            quarter: this.state.quarter,
            fine: this.state.fine,
            crop: [crop.x0 / canvas.width, crop.y0 / canvas.height,
                   crop.x1 / canvas.width, crop.y1 / canvas.height],
        };
    }

    /** Go back to the uploaded image, without rotation or crop. */
    reset() {
        this.setParams(null);
    }

    /** Suggest the rotation and frame of the automatic retouch, without applying. */
    async autoRetouch() {
        this.state.auto = true;
        this.state.error = null;
        try {
            this.setParams(await this.orm.call(
                "hr.expense", "expense_scan_auto_retouch_params", [[this.props.resId]]));
        } catch (error) {
            this.state.error = _t("The automatic retouch failed.");
            throw error;
        } finally {
            this.state.auto = false;
        }
    }

    /** Reset the frame to the whole image. */
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

    /** Draw the rotated image and the frame. */
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
        // Darken the area outside the frame.
        ctx.fillStyle = "rgba(0, 0, 0, 0.5)";
        ctx.fillRect(0, 0, width, crop.y0);
        ctx.fillRect(0, crop.y1, width, height - crop.y1);
        ctx.fillRect(0, crop.y0, crop.x0, crop.y1 - crop.y0);
        ctx.fillRect(crop.x1, crop.y0, width - crop.x1, crop.y1 - crop.y0);
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 2;
        ctx.strokeRect(crop.x0, crop.y0, crop.x1 - crop.x0, crop.y1 - crop.y0);
        // Corner handles drawn towards the inside of the frame, so that they
        // stay visible at the canvas edge; dark outline under a white line.
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
    // Cropping (pointer events: mouse and touch)
    // ------------------------------------------------------------------

    /** Pointer position in canvas pixels. */
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
            // The corner moves its two edges, without crossing the opposite ones.
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
    // Applying
    // ------------------------------------------------------------------

    async onApply() {
        this.state.busy = true;
        this.state.error = null;
        try {
            if (await this.props.apply(this.compose(), this.currentParams())) {
                this.props.close();
                return;
            }
            this.state.error = _t(
                "The form cannot be saved as it is: correct it, then try again.");
            this.state.busy = false;
        } catch (error) {
            this.state.error = _t("The retouch could not be saved. Try again.");
            this.state.busy = false;
            throw error;
        }
    }

    /**
     * Final image as JPEG, base64 encoded without the header.
     *
     * Drawn directly into a canvas the size of the frame, without an
     * intermediate canvas for the whole rotated image: for a large photo,
     * that one would exceed the limit of mobile browsers. Size capped at
     * MAX_OUTPUT_PIXELS.
     */
    compose() {
        const iw = this.image.naturalWidth;
        const ih = this.image.naturalHeight;
        const bounds = this.rotatedBounds(iw, ih);
        // From preview coordinates to full resolution.
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
        // White background for the corners uncovered by the rotation: a
        // black one would be read as text.
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
