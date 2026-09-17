/** @odoo-module **/
// Copyright 2026 Yves Vallée
// License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
/**
 * Télécharge la fiche de frais produite, puis ferme la fenêtre d'export.
 *
 * Un lien ordinaire laissait la fenêtre ouverte ; ouvrir un nouvel onglet
 * serait bloqué par les navigateurs qui filtrent les fenêtres surgissantes,
 * l'ouverture n'étant plus liée au clic une fois le fichier fabriqué.
 */
import { download } from "@web/core/network/download";
import { registry } from "@web/core/registry";

async function expenseScanDownload(env, action) {
    await download({ url: action.params.url, data: {} });
    return { type: "ir.actions.act_window_close" };
}

registry.category("actions").add("expense_scan_download", expenseScanDownload);
