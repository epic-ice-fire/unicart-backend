import "package:flutter/material.dart";
import "package:flutter/services.dart";
import "../../services/lobby_service.dart";

class AdminDashboardScreen extends StatefulWidget {
  final String token;
  const AdminDashboardScreen({super.key, required this.token});

  @override
  State<AdminDashboardScreen> createState() => _AdminDashboardScreenState();
}

class _AdminDashboardScreenState extends State<AdminDashboardScreen> {
  bool isLoading = true;
  bool isBusy = false;
  String? error;
  Map<String, dynamic>? dashboardData;
  final TextEditingController targetController = TextEditingController();

  @override
  void initState() {
    super.initState();
    loadDashboard();
  }

  @override
  void dispose() {
    targetController.dispose();
    super.dispose();
  }

  Future<void> loadDashboard() async {
    setState(() { isLoading = true; error = null; });
    try {
      final data = await LobbyService.adminDashboard(widget.token);
      final openLobby = data["current_open_lobby"] as Map<String, dynamic>?;
      if (openLobby != null) {
        targetController.text =
            (openLobby["target_item_amount"] ?? "").toString();
      }
      setState(() => dashboardData = data);
    } catch (e) {
      setState(() => error = e.toString());
    } finally {
      if (mounted) setState(() => isLoading = false);
    }
  }

  Future<void> updateMov() async {
    final mov = int.tryParse(targetController.text.trim());
    if (mov == null || mov <= 0) { showMessage("Enter a valid MOV."); return; }
    setState(() => isBusy = true);
    try {
      await LobbyService.adminUpdateOpenLobbyTarget(
        widget.token, targetItemAmount: mov,
      );
      await loadDashboard();
      showMessage("MOV updated successfully.", isSuccess: true);
    } catch (e) {
      showMessage(e.toString());
    } finally {
      if (mounted) setState(() => isBusy = false);
    }
  }

  Future<void> updateBatchStatus({
    required int lobbyId,
    required String newStatus,
  }) async {
    setState(() => isBusy = true);
    try {
      await LobbyService.adminUpdateBatchStatus(
        widget.token, lobbyId: lobbyId, newStatus: newStatus,
      );
      await loadDashboard();
      showMessage("Batch status updated.", isSuccess: true);
    } catch (e) {
      showMessage(e.toString());
    } finally {
      if (mounted) setState(() => isBusy = false);
    }
  }

  Future<void> forceRemoveItem(int itemId, String itemLink, bool isPaid) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Row(
          children: [
            const Icon(Icons.warning_amber_rounded, color: Color(0xFFB42318)),
            const SizedBox(width: 8),
            const Text("Remove Item", style: TextStyle(fontWeight: FontWeight.w800)),
          ],
        ),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: const Color(0xFFFEF3F2),
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: const Color(0xFFFECACA)),
              ),
              child: Text(
                itemLink,
                style: const TextStyle(
                  fontWeight: FontWeight.w700, color: Color(0xFF101828), fontSize: 13,
                ),
              ),
            ),
            const SizedBox(height: 14),
            if (isPaid)
              Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: const Color(0xFFFFF7E6),
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: const Color(0xFFFAC515)),
                ),
                child: const Text(
                  "⚠️ This item has been paid for. Removing it will NOT issue a refund "
                  "and the batch total will drop below the original target.",
                  style: TextStyle(
                    color: Color(0xFF7A2E0E), fontWeight: FontWeight.w700, fontSize: 13,
                  ),
                ),
              ),
            const SizedBox(height: 14),
            const Text(
              "No-Refund Policy",
              style: TextStyle(fontWeight: FontWeight.w800, fontSize: 15),
            ),
            const SizedBox(height: 6),
            const Text(
              "UniCart maintains a strict no-refund policy for items removed "
              "due to fraudulent submissions, fabricated amounts, or violations "
              "of our platform guidelines. By proceeding, you confirm that this "
              "item has been reviewed and found to be in breach of UniCart's "
              "Terms of Service. The user will be notified via email.",
              style: TextStyle(color: Color(0xFF475467), fontSize: 13, height: 1.5),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text("Cancel"),
          ),
          ElevatedButton(
            style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFFB42318)),
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text(
              "Remove (No Refund)",
              style: TextStyle(color: Colors.white),
            ),
          ),
        ],
      ),
    );

    if (confirmed != true) return;

    setState(() => isBusy = true);
    try {
      final result = await LobbyService.adminForceRemoveItem(widget.token, itemId: itemId);
      await loadDashboard();
      final isUnderfunded = result["lobby_is_underfunded"] == true;
      final gap = result["underfunded_gap"] ?? 0;
      if (isUnderfunded) {
        showMessage(
          "Item #$itemId removed. ⚠️ Batch is now ₦$gap below target due to removal.",
          isSuccess: false,
        );
      } else {
        showMessage("Item #$itemId removed successfully.", isSuccess: true);
      }
    } catch (e) {
      showMessage(e.toString());
    } finally {
      if (mounted) setState(() => isBusy = false);
    }
  }

  Future<void> copyItemLink(String link) async {
    if (link.trim().isEmpty) { showMessage("No item link."); return; }
    await Clipboard.setData(ClipboardData(text: link));
    showMessage("Link copied.", isSuccess: true);
  }

  void showMessage(String message, {bool isSuccess = false}) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Row(
          children: [
            Icon(
              isSuccess ? Icons.check_circle_outline : Icons.info_outline,
              color: Colors.white, size: 18,
            ),
            const SizedBox(width: 8),
            Expanded(child: Text(message)),
          ],
        ),
        backgroundColor: isSuccess ? const Color(0xFF027A48) : const Color(0xFF344054),
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        margin: const EdgeInsets.all(16),
      ),
    );
  }

  String prettyStatus(String s) {
    switch (s) {
      case "triggered": return "Triggered";
      case "processing": return "Processing";
      case "in_transit": return "In Transit";
      case "completed": return "Completed";
      case "open": return "Open";
      case "paid": return "Paid";
      case "pending": return "Pending";
      case "failed": return "Failed";
      case "abandoned": return "Abandoned";
      case "unpaid": return "Unpaid";
      default: return s;
    }
  }

  Color statusBg(String s) {
    switch (s) {
      case "triggered": return const Color(0xFFFFF7E6);
      case "processing": return const Color(0xFFEFF8FF);
      case "in_transit": return const Color(0xFFF4F3FF);
      case "completed": case "paid": return const Color(0xFFECFDF3);
      case "pending": return const Color(0xFFFFFAEB);
      case "failed": case "abandoned": return const Color(0xFFFEF3F2);
      default: return const Color(0xFFF9FAFB);
    }
  }

  Color statusBorder(String s) {
    switch (s) {
      case "triggered": return const Color(0xFFFAC515);
      case "processing": return const Color(0xFF53B1FD);
      case "in_transit": return const Color(0xFF9E77ED);
      case "completed": case "paid": return const Color(0xFF4BB543);
      case "pending": return const Color(0xFFF79009);
      case "failed": case "abandoned": return const Color(0xFFF04438);
      default: return const Color(0xFFE4E7EC);
    }
  }

  IconData statusIcon(String s) {
    switch (s) {
      case "triggered": return Icons.flag_outlined;
      case "processing": return Icons.inventory_2_outlined;
      case "in_transit": return Icons.local_shipping_outlined;
      case "completed": return Icons.check_circle_outline;
      case "open": return Icons.lock_open_outlined;
      case "paid": return Icons.lock_outline;
      case "pending": return Icons.hourglass_bottom_outlined;
      case "failed": case "abandoned": return Icons.error_outline;
      default: return Icons.info_outline;
    }
  }

  Widget buildStatusBadge(String status) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: statusBg(status), borderRadius: BorderRadius.circular(999),
        border: Border.all(color: statusBorder(status)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(statusIcon(status), size: 16, color: const Color(0xFF344054)),
          const SizedBox(width: 6),
          Text(prettyStatus(status),
              style: const TextStyle(color: Color(0xFF344054), fontWeight: FontWeight.w700)),
        ],
      ),
    );
  }

  /// ── Underfunded warning banner ──────────────────────────────────────────────
  /// Shown inside a batch card when a paid item was force-removed AFTER trigger.
  /// The batch is still valid and must be processed — this is just a warning.
  Widget buildUnderfundedBanner(int gap) {
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.only(bottom: 14),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: const Color(0xFFFFF7E6),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: const Color(0xFFFAC515)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Icon(Icons.warning_amber_rounded, color: Color(0xFFB54708), size: 20),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  "Batch underfunded after fraud removal",
                  style: TextStyle(
                    fontWeight: FontWeight.w800,
                    color: Color(0xFF7A2E0E),
                    fontSize: 13,
                  ),
                ),
                const SizedBox(height: 4),
                Text(
                  "A paid item was removed from this batch after it was triggered. "
                  "The batch total is now ₦${gap.toString()} below the original target. "
                  "This batch is still valid — all remaining paid items must still be processed. "
                  "The affected user has been notified.",
                  style: const TextStyle(
                    color: Color(0xFF7A2E0E), fontSize: 12, height: 1.5,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget buildItemLabel(String label) {
    Color bg; Color border;
    switch (label) {
      case "PAID (LOCKED)":
        bg = const Color(0xFFECFDF3); border = const Color(0xFF4BB543); break;
      case "REMOVED":
        bg = const Color(0xFFFEF3F2); border = const Color(0xFFF04438); break;
      case "PAYMENT PENDING":
        bg = const Color(0xFFFFFAEB); border = const Color(0xFFF79009); break;
      default:
        bg = const Color(0xFFF2F4F7); border = const Color(0xFFD0D5DD);
    }
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: bg, borderRadius: BorderRadius.circular(999),
        border: Border.all(color: border),
      ),
      child: Text(label,
          style: const TextStyle(fontWeight: FontWeight.w700, color: Color(0xFF344054), fontSize: 12)),
    );
  }

  Widget statChip(String label, String value) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      decoration: BoxDecoration(
        color: Colors.white.withOpacity(0.12), borderRadius: BorderRadius.circular(18),
        border: Border.all(color: Colors.white.withOpacity(0.18)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label,
              style: const TextStyle(fontSize: 12, color: Color(0xFFEAF7EF), fontWeight: FontWeight.w600)),
          const SizedBox(height: 6),
          Text(value,
              style: const TextStyle(fontSize: 16, color: Colors.white, fontWeight: FontWeight.w800)),
        ],
      ),
    );
  }

  Widget statTile(String label, String value) {
    return Container(
      width: 180, padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: const Color(0xFFF9FAFB), borderRadius: BorderRadius.circular(18),
        border: Border.all(color: const Color(0xFFE4E7EC)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label,
              style: const TextStyle(fontSize: 12, color: Color(0xFF667085), fontWeight: FontWeight.w600)),
          const SizedBox(height: 6),
          Text(value,
              style: const TextStyle(fontSize: 16, color: Color(0xFF101828), fontWeight: FontWeight.w800)),
        ],
      ),
    );
  }

  Widget infoPill(String value) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: Colors.white, borderRadius: BorderRadius.circular(999),
        border: Border.all(color: const Color(0xFFE4E7EC)),
      ),
      child: Text(value,
          style: const TextStyle(color: Color(0xFF344054), fontWeight: FontWeight.w600, fontSize: 13)),
    );
  }

  /// Shows a price-verification checklist before the admin starts processing.
  /// Forces the admin to confirm they have manually verified all item prices
  /// against the guest (logged-out) price on Temu before placing the order.
  Future<void> startProcessingWithPriceCheck({
    required int lobbyId,
    required List<Map<String, dynamic>> items,
  }) async {
    // Build a compact item list for the dialog
    final paidItems = items.where((i) => i["is_paid"] == true && i["is_active"] == true).toList();

    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Row(
          children: [
            Icon(Icons.fact_check_outlined, color: Color(0xFF1F7A4C)),
            SizedBox(width: 8),
            Flexible(
              child: Text("Verify prices before processing",
                  style: TextStyle(fontWeight: FontWeight.w800, fontSize: 16)),
            ),
          ],
        ),
        content: SizedBox(
          width: 480,
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: const Color(0xFFFFF7E6),
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(color: const Color(0xFFFAC515)),
                  ),
                  child: const Text(
                    "UniCart uses guest-price standardisation. Before clicking "
                    "Start Processing you must manually verify every item price "
                    "on Temu while logged OUT (or in a private window).",
                    style: TextStyle(
                        fontSize: 13, color: Color(0xFF7A2E0E), height: 1.5,
                        fontWeight: FontWeight.w600),
                  ),
                ),
                const SizedBox(height: 16),
                const Text("Paid items in this batch:",
                    style: TextStyle(fontWeight: FontWeight.w800, fontSize: 14)),
                const SizedBox(height: 8),
                if (paidItems.isEmpty)
                  const Text("No paid items found.",
                      style: TextStyle(color: Color(0xFF667085)))
                else
                  ...paidItems.map((item) => Container(
                    margin: const EdgeInsets.only(bottom: 8),
                    padding: const EdgeInsets.all(10),
                    decoration: BoxDecoration(
                      color: const Color(0xFFF9FAFB),
                      borderRadius: BorderRadius.circular(10),
                      border: Border.all(color: const Color(0xFFE4E7EC)),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          item["item_link"]?.toString() ?? "Unknown link",
                          style: const TextStyle(
                              fontWeight: FontWeight.w700,
                              fontSize: 12,
                              color: Color(0xFF101828)),
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                        ),
                        const SizedBox(height: 4),
                        Row(
                          children: [
                            const Icon(Icons.person_outline,
                                size: 12, color: Color(0xFF667085)),
                            const SizedBox(width: 4),
                            Expanded(
                              child: Text(
                                item["user_email"]?.toString() ?? "",
                                style: const TextStyle(
                                    fontSize: 11, color: Color(0xFF667085)),
                                overflow: TextOverflow.ellipsis,
                              ),
                            ),
                            const SizedBox(width: 8),
                            Container(
                              padding: const EdgeInsets.symmetric(
                                  horizontal: 8, vertical: 3),
                              decoration: BoxDecoration(
                                color: const Color(0xFFECFDF3),
                                borderRadius: BorderRadius.circular(999),
                                border: Border.all(color: const Color(0xFF4BB543)),
                              ),
                              child: Text(
                                "₦${item["item_amount"]}  (submitted guest price)",
                                style: const TextStyle(
                                    fontSize: 11,
                                    fontWeight: FontWeight.w700,
                                    color: Color(0xFF027A48)),
                              ),
                            ),
                          ],
                        ),
                      ],
                    ),
                  )),
                const SizedBox(height: 16),
                const Text("Admin checklist:",
                    style: TextStyle(fontWeight: FontWeight.w800, fontSize: 14)),
                const SizedBox(height: 8),
                const Text(
                  "✅  I am logged OUT of Temu (or in a private window)"
                  "✅  I have checked each item link above as a guest"
                  "✅  The submitted prices match what I see as a guest"
                  "✅  Any price discrepancies have been resolved (force-removed)"
                  "✅  I am ready to place the group order",
                  style: TextStyle(
                      fontSize: 13, color: Color(0xFF344054), height: 1.7),
                ),
                const SizedBox(height: 12),
                Container(
                  padding: const EdgeInsets.all(10),
                  decoration: BoxDecoration(
                    color: const Color(0xFFFEF3F2),
                    borderRadius: BorderRadius.circular(10),
                    border: Border.all(color: const Color(0xFFFECACA)),
                  ),
                  child: const Text(
                    "By clicking Start Processing you confirm all prices have been "
                    "verified against guest pricing. This action cannot be undone.",
                    style: TextStyle(
                        fontSize: 12,
                        color: Color(0xFFB42318),
                        fontWeight: FontWeight.w600),
                  ),
                ),
              ],
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text("Cancel — verify first"),
          ),
          ElevatedButton.icon(
            style: ElevatedButton.styleFrom(
                backgroundColor: const Color(0xFF1F7A4C)),
            onPressed: () => Navigator.pop(ctx, true),
            icon: const Icon(Icons.play_circle_outline, color: Colors.white),
            label: const Text("Start Processing",
                style: TextStyle(color: Colors.white)),
          ),
        ],
      ),
    );

    if (confirmed != true) return;
    await updateBatchStatus(lobbyId: lobbyId, newStatus: "processing");
  }

  Widget buildBatchActionButtons(Map<String, dynamic> batch) {
    final status = batch["status"]?.toString() ?? "";
    final lobbyId = batch["lobby_id"] as int;
    final items = ((batch["items"] as List?) ?? []).cast<Map<String, dynamic>>();

    if (status == "triggered") {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // Price verification reminder banner
          Container(
            padding: const EdgeInsets.all(12),
            margin: const EdgeInsets.only(bottom: 10),
            decoration: BoxDecoration(
              color: const Color(0xFFEFF8FF),
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: const Color(0xFF53B1FD)),
            ),
            child: const Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(Icons.info_outline, size: 16, color: Color(0xFF175CD3)),
                SizedBox(width: 8),
                Expanded(
                  child: Text(
                    "Before processing: log OUT of Temu, open each item link "
                    "in a private window, and confirm the submitted price matches "
                    "the guest price. Use Force Remove for any discrepancies.",
                    style: TextStyle(
                        fontSize: 12,
                        color: Color(0xFF175CD3),
                        fontWeight: FontWeight.w600,
                        height: 1.5),
                  ),
                ),
              ],
            ),
          ),
          Align(
            alignment: Alignment.centerRight,
            child: ElevatedButton.icon(
              onPressed: isBusy
                  ? null
                  : () => startProcessingWithPriceCheck(
                        lobbyId: lobbyId, items: items),
              icon: const Icon(Icons.fact_check_outlined),
              label: const Text("Verify Prices & Start Processing"),
            ),
          ),
        ],
      );
    }
    if (status == "processing") {
      return Align(
        alignment: Alignment.centerRight,
        child: ElevatedButton.icon(
          onPressed: isBusy
              ? null
              : () => updateBatchStatus(lobbyId: lobbyId, newStatus: "in_transit"),
          icon: const Icon(Icons.local_shipping_outlined),
          label: const Text("Mark In Transit"),
        ),
      );
    }
    if (status == "in_transit") {
      return Align(
        alignment: Alignment.centerRight,
        child: ElevatedButton.icon(
          onPressed: isBusy
              ? null
              : () => updateBatchStatus(lobbyId: lobbyId, newStatus: "completed"),
          icon: const Icon(Icons.check_circle_outline),
          label: const Text("Mark Completed"),
        ),
      );
    }
    if (status == "completed") {
      return const Align(
        alignment: Alignment.centerRight,
        child: Chip(
          avatar: Icon(Icons.check_circle_outline, size: 18),
          label: Text("Completed"),
          backgroundColor: Color(0xFFECFDF3),
          side: BorderSide(color: Color(0xFF4BB543)),
        ),
      );
    }
    return const SizedBox.shrink();
  }

  @override
  Widget build(BuildContext context) {
    final openLobby = dashboardData?["current_open_lobby"] as Map<String, dynamic>?;
    final triggeredBatches =
        ((dashboardData?["triggered_batches"] as List?) ?? [])
            .cast<Map<String, dynamic>>();
    final triggeredCount = dashboardData?["triggered_batch_count"] ?? 0;

    final openLobbyId = openLobby?["lobby_id"]?.toString() ?? "-";
    final openLobbyStatus = openLobby?["status"]?.toString() ?? "Unknown";
    final openAmount = "₦${openLobby?["current_item_amount"] ?? 0}";
    final openTarget = "₦${openLobby?["target_item_amount"] ?? 0}";
    final openMembers = "${openLobby?["member_count"] ?? 0}";

    return Scaffold(
      appBar: AppBar(
        title: const Text("Admin Dashboard",
            style: TextStyle(fontWeight: FontWeight.w800)),
        actions: [
          IconButton(
            onPressed: isLoading || isBusy ? null : loadDashboard,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: isLoading
          ? const Center(child: CircularProgressIndicator())
          : SafeArea(
              child: Center(
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 1080),
                  child: RefreshIndicator(
                    onRefresh: loadDashboard,
                    child: ListView(
                      physics: const AlwaysScrollableScrollPhysics(),
                      padding: const EdgeInsets.all(16),
                      children: [
                        if (error != null)
                          Container(
                            margin: const EdgeInsets.only(bottom: 16),
                            padding: const EdgeInsets.all(14),
                            decoration: BoxDecoration(
                              color: const Color(0xFFFEF3F2),
                              borderRadius: BorderRadius.circular(16),
                              border: Border.all(color: const Color(0xFFFECACA)),
                            ),
                            child: Text(error!,
                                style: const TextStyle(
                                    color: Color(0xFFB42318), fontWeight: FontWeight.w600)),
                          ),

                        // Hero header
                        Container(
                          padding: const EdgeInsets.all(22),
                          decoration: BoxDecoration(
                            gradient: const LinearGradient(
                              colors: [Color(0xFF1F7A4C), Color(0xFF2E8B57)],
                              begin: Alignment.topLeft, end: Alignment.bottomRight,
                            ),
                            borderRadius: BorderRadius.circular(28),
                          ),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              const Text("Batch operations control center",
                                  style: TextStyle(
                                      fontSize: 26, height: 1.2, fontWeight: FontWeight.w800,
                                      color: Colors.white)),
                              const SizedBox(height: 8),
                              const Text(
                                  "Track the open lobby, manage batches, and handle the delivery pipeline.",
                                  style: TextStyle(fontSize: 14, color: Color(0xFFEAF7EF))),
                              const SizedBox(height: 20),
                              Wrap(
                                spacing: 10, runSpacing: 10,
                                children: [
                                  statChip("Open lobby", "#$openLobbyId"),
                                  statChip("Status", prettyStatus(openLobbyStatus)),
                                  statChip("Pipeline batches", "$triggeredCount"),
                                  statChip("Open members", openMembers),
                                ],
                              ),
                            ],
                          ),
                        ),

                        const SizedBox(height: 18),

                        // Current open lobby card
                        Card(
                          elevation: 0,
                          child: Padding(
                            padding: const EdgeInsets.all(22),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                const Text("Current open lobby",
                                    style: TextStyle(fontSize: 20, fontWeight: FontWeight.w800)),
                                const SizedBox(height: 6),
                                const Text(
                                    "The lobby students are currently joining and adding items into.",
                                    style: TextStyle(color: Color(0xFF667085))),
                                const SizedBox(height: 16),
                                if (openLobby == null)
                                  const Text("No open lobby found.",
                                      style: TextStyle(color: Color(0xFF667085)))
                                else
                                  Column(
                                    crossAxisAlignment: CrossAxisAlignment.start,
                                    children: [
                                      Wrap(
                                        spacing: 10, runSpacing: 10,
                                        children: [
                                          statTile("Lobby ID", "#$openLobbyId"),
                                          statTile("Status", prettyStatus(openLobbyStatus)),
                                          statTile("Paid total", openAmount),
                                          statTile("Target", openTarget),
                                          statTile("Members", openMembers),
                                        ],
                                      ),
                                      const SizedBox(height: 18),
                                      TextField(
                                        controller: targetController,
                                        keyboardType: TextInputType.number,
                                        decoration: const InputDecoration(
                                          labelText: "Update MOV / target amount (₦)",
                                          prefixIcon: Icon(Icons.edit_outlined),
                                        ),
                                      ),
                                      const SizedBox(height: 12),
                                      SizedBox(
                                        width: double.infinity,
                                        child: ElevatedButton(
                                          onPressed: isBusy ? null : updateMov,
                                          child: const Text("Update MOV"),
                                        ),
                                      ),
                                    ],
                                  ),
                              ],
                            ),
                          ),
                        ),

                        const SizedBox(height: 18),

                        // Batch pipeline card
                        Card(
                          elevation: 0,
                          child: Padding(
                            padding: const EdgeInsets.all(22),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text("Batch pipeline ($triggeredCount)",
                                    style: const TextStyle(
                                        fontSize: 20, fontWeight: FontWeight.w800)),
                                const SizedBox(height: 6),
                                const Text(
                                    "Manage triggered, processing, in transit, and completed batches. "
                                    "You can force-remove any item (including paid ones) if it is fraudulent.",
                                    style: TextStyle(color: Color(0xFF667085))),
                                const SizedBox(height: 16),
                                if (triggeredBatches.isEmpty)
                                  Container(
                                    width: double.infinity, padding: const EdgeInsets.all(24),
                                    decoration: BoxDecoration(
                                      color: const Color(0xFFF9FAFB),
                                      borderRadius: BorderRadius.circular(18),
                                      border: Border.all(color: const Color(0xFFE4E7EC)),
                                    ),
                                    child: const Column(
                                      children: [
                                        Icon(Icons.inbox_outlined, size: 36, color: Color(0xFFD0D5DD)),
                                        SizedBox(height: 10),
                                        Text("No batches in the pipeline yet.",
                                            style: TextStyle(color: Color(0xFF667085))),
                                      ],
                                    ),
                                  )
                                else
                                  ...triggeredBatches.map((batch) {
                                    final items =
                                        ((batch["items"] as List?) ?? [])
                                            .cast<Map<String, dynamic>>();
                                    final batchStatus = batch["status"]?.toString() ?? "";
                                    // ── Underfunded flag from backend ──────────
                                    final isUnderfunded = batch["is_underfunded"] == true;
                                    final underfundedGap = batch["underfunded_gap"] as int? ?? 0;

                                    return Container(
                                      margin: const EdgeInsets.only(bottom: 14),
                                      decoration: BoxDecoration(
                                        color: const Color(0xFFF9FAFB),
                                        borderRadius: BorderRadius.circular(20),
                                        border: Border.all(
                                          color: isUnderfunded
                                              ? const Color(0xFFFAC515)
                                              : const Color(0xFFE4E7EC),
                                          width: isUnderfunded ? 1.5 : 1.0,
                                        ),
                                      ),
                                      child: ExpansionTile(
                                        tilePadding: const EdgeInsets.symmetric(
                                            horizontal: 18, vertical: 8),
                                        childrenPadding:
                                            const EdgeInsets.fromLTRB(18, 0, 18, 18),
                                        title: Text("Batch #${batch["lobby_id"]}",
                                            style: const TextStyle(
                                                fontWeight: FontWeight.w800,
                                                color: Color(0xFF101828))),
                                        subtitle: Padding(
                                          padding: const EdgeInsets.only(top: 6),
                                          child: Column(
                                            crossAxisAlignment: CrossAxisAlignment.start,
                                            children: [
                                              Text(
                                                  "Paid total: ₦${batch["final_item_amount"]} / ₦${batch["target_item_amount"]}"),
                                              if (isUnderfunded)
                                                Padding(
                                                  padding: const EdgeInsets.only(top: 4),
                                                  child: Row(
                                                    children: [
                                                      const Icon(Icons.warning_amber_rounded,
                                                          size: 14, color: Color(0xFFB54708)),
                                                      const SizedBox(width: 4),
                                                      Text(
                                                        "Underfunded by ₦$underfundedGap (fraud removal)",
                                                        style: const TextStyle(
                                                          fontSize: 12,
                                                          color: Color(0xFFB54708),
                                                          fontWeight: FontWeight.w700,
                                                        ),
                                                      ),
                                                    ],
                                                  ),
                                                ),
                                            ],
                                          ),
                                        ),
                                        children: [
                                          // ── Underfunded banner ───────────────
                                          if (isUnderfunded)
                                            buildUnderfundedBanner(underfundedGap),

                                          Wrap(
                                            spacing: 10, runSpacing: 10,
                                            children: [
                                              buildStatusBadge(batchStatus),
                                              infoPill("Members: ${batch["member_count"]}"),
                                              infoPill("Items: ${items.length}"),
                                              infoPill("Paying members: ${batch["paid_member_count"]}"),
                                              infoPill("Entry fee revenue: ₦${batch["paid_total_ngn"]}"),
                                            ],
                                          ),
                                          const SizedBox(height: 14),
                                          buildBatchActionButtons(batch),
                                          const SizedBox(height: 14),
                                          const Divider(),
                                          const SizedBox(height: 8),
                                          const Align(
                                            alignment: Alignment.centerLeft,
                                            child: Text("Items in this batch",
                                                style: TextStyle(
                                                    fontWeight: FontWeight.w800,
                                                    color: Color(0xFF101828))),
                                          ),
                                          const SizedBox(height: 10),
                                          if (items.isEmpty)
                                            const Text("No items found.",
                                                style: TextStyle(color: Color(0xFF667085)))
                                          else
                                            ...items.map((item) {
                                              final itemId = item["item_id"] as int? ?? 0;
                                              final itemLink =
                                                  item["item_link"]?.toString() ?? "";
                                              final itemAmount = item["item_amount"] ?? 0;
                                              final userEmail =
                                                  item["user_email"]?.toString() ?? "Unknown";
                                              final itemLabel =
                                                  item["item_label"]?.toString() ?? "ACTIVE";
                                              final paymentStatus =
                                                  item["item_payment_status"]?.toString() ?? "unpaid";
                                              final paymentAmount =
                                                  item["item_payment_amount_ngn"] ?? 0;
                                              final isLocked = item["is_locked"] == true;
                                              final isPaid = item["is_paid"] == true;
                                              final isActive = item["is_active"] == true;
                                              final ref =
                                                  item["item_payment_reference"]?.toString();

                                              return Container(
                                                margin: const EdgeInsets.only(bottom: 10),
                                                padding: const EdgeInsets.all(14),
                                                decoration: BoxDecoration(
                                                  color: isActive
                                                      ? Colors.white
                                                      : const Color(0xFFFEF3F2),
                                                  borderRadius: BorderRadius.circular(14),
                                                  border: Border.all(
                                                    color: isActive
                                                        ? const Color(0xFFE4E7EC)
                                                        : const Color(0xFFFECACA),
                                                  ),
                                                ),
                                                child: Column(
                                                  crossAxisAlignment: CrossAxisAlignment.start,
                                                  children: [
                                                    Text(itemLink,
                                                        style: const TextStyle(
                                                            fontWeight: FontWeight.w700,
                                                            color: Color(0xFF101828)),
                                                        maxLines: 2,
                                                        overflow: TextOverflow.ellipsis),
                                                    const SizedBox(height: 10),
                                                    Wrap(
                                                      spacing: 8, runSpacing: 8,
                                                      children: [
                                                        buildItemLabel(itemLabel),
                                                        infoPill("₦$itemAmount"),
                                                        infoPill(userEmail),
                                                        infoPill("Payment: ₦$paymentAmount"),
                                                        infoPill(
                                                            "Status: ${prettyStatus(paymentStatus)}"),
                                                        infoPill(
                                                            "Locked: ${isLocked ? "Yes" : "No"}"),
                                                        if (ref != null && ref.isNotEmpty)
                                                          infoPill("Ref: $ref"),
                                                      ],
                                                    ),
                                                    const SizedBox(height: 10),
                                                    Wrap(
                                                      spacing: 8, runSpacing: 8,
                                                      alignment: WrapAlignment.end,
                                                      children: [
                                                        TextButton.icon(
                                                          onPressed: isBusy
                                                              ? null
                                                              : () => copyItemLink(itemLink),
                                                          icon: const Icon(
                                                              Icons.copy_outlined, size: 15),
                                                          label: const Text("Copy link"),
                                                        ),
                                                        if (isActive)
                                                          OutlinedButton.icon(
                                                            style: OutlinedButton.styleFrom(
                                                              foregroundColor:
                                                                  const Color(0xFFB42318),
                                                              side: const BorderSide(
                                                                  color: Color(0xFFFECACA)),
                                                            ),
                                                            onPressed: isBusy
                                                                ? null
                                                                : () => forceRemoveItem(
                                                                    itemId, itemLink, isPaid),
                                                            icon: const Icon(
                                                                Icons.delete_forever_outlined,
                                                                size: 15),
                                                            label: const Text("Force Remove"),
                                                          ),
                                                      ],
                                                    ),
                                                  ],
                                                ),
                                              );
                                            }),
                                        ],
                                      ),
                                    );
                                  }),
                              ],
                            ),
                          ),
                        ),

                        const SizedBox(height: 32),
                      ],
                    ),
                  ),
                ),
              ),
            ),
    );
  }
}