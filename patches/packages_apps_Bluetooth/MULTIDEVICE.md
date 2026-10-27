# A2DP Sink multi-device ("one-to-two", feature F6) — design & implementation guide

**Status:** the device-addressing prerequisite is implemented (patch
`0001-a2dpsink-Bind-each-StreamHandler-to-its-own-BluetoothDevice.patch`).
The full per-device state-machine rewrite that keeps two ACL links up at the
same time is **specified here but intentionally not shipped as a finished
patch**, because AOSP 8.1 has no in-tree template for it and it cannot be
validated without a full AOSP build plus two physical source devices.  Shipping
~800 lines of un-compilable, un-testable architectural Java as a "done" patch
would be dishonest; this document is the blueprint for completing it under a
build + hardware test cycle.

---

## 1. Why 8.1 cannot do F6 out of the box

Both the A2DP **source** (`a2dp/A2dpStateMachine.java`) and **sink**
(`a2dpsink/A2dpSinkStateMachine.java`) profiles in `android-8.1.0_r80` use a
**single-active-device** model built on four mutually exclusive fields:

```java
private BluetoothDevice mCurrentDevice = null;   // connected now
private BluetoothDevice mTargetDevice  = null;   // we are connecting to it
private BluetoothDevice mIncomingDevice = null;  // it is connecting to us
private BluetoothDevice mPlayingDevice = null;   // currently streaming
```

The class comment states the invariant explicitly: *when `mIncomingDevice` is
not null, both `mCurrentDevice` and `mTargetDevice` are null*, etc.  In the
`Connected` state, a `CONNECT` request for a **new** device first tears the
existing one down:

```java
// A2dpSinkStateMachine.Connected.processMessage(), case CONNECT:
if (!disconnectA2dpNative(getByteAddress(mCurrentDevice))) { ... }
mTargetDevice = device;
mStreaming.obtainMessage(A2dpSinkStreamHandler.DISCONNECT).sendToTarget();
transitionTo(mPending);
```

So connecting device B **disconnects** device A.  `getConnectedDevices()` can
therefore only ever return one device.

> **Correction to the plan (section 16.6):** the plan says to "copy the
> same-version source-side `A2dpService`, which uses
> `HashMap<BluetoothDevice, StateMachine>`".  That is **not true for 8.1** — the
> 8.1 source profile uses the identical four-field single-device model.  The
> per-device `HashMap<BluetoothDevice, StateMachine>` architecture only landed
> in AOSP around Android 10 (Q).  So F6 on 8.1 is a **new** architecture, not a
> copy; the Q-era source profile is the reference to port *from*, not the 8.1
> source profile.

The native layer is **already** ready for two links:
`system/bt/include/bt_target.h` defines `AVDT_NUM_LINKS 2`, `AVDT_NUM_SEPS 6`,
`AVDT_NUM_TC_TBL 6`, and `MAX_L2CAP_LINKS 7`.  The device tree sets
`BTA_AV_MAX_STREAMS 2` in `bdroid_buildcfg.h`.  The blocker is purely in the
Java profile layer.

---

## 2. Target architecture

Mirror the Android 10 A2DP source design, adapted to the sink:

```
A2dpSinkService
  └─ HashMap<BluetoothDevice, A2dpSinkStateMachine> mStateMachines   (one SM per device)
  └─ BluetoothDevice mActiveDevice                                   (the one streaming)

A2dpSinkStateMachine (per device, single-device internally)
  └─ holds exactly one BluetoothDevice (passed to make())
  └─ Disconnected → Pending → Connected  (unchanged shape, but no cross-device fields)
  └─ owns its A2dpSinkStreamHandler (already bound to its device — patch 0001)
```

### 2.1 `A2dpSinkService.java`

Replace the single `mStateMachine` with a map:

```java
private final HashMap<BluetoothDevice, A2dpSinkStateMachine> mStateMachines =
        new HashMap<>();
private static final int MAX_CONNECTED_AUDIO_DEVICES = 2;   // F6: one-to-two
private BluetoothDevice mActiveDevice = null;
```

* `start()` / `stop()` / `cleanup()`: create/quit/clear per-device SMs lazily
  instead of a single one.
* `getConnectionState(device)`: look up `mStateMachines.get(device)`; return
  `STATE_DISCONNECTED` when absent.
* `getConnectedDevices()` / `getDevicesMatchingConnectionStates(states)`:
  iterate the map and collect devices whose SM reports the matching state.
* `connect(device)`: **admission control** —
  ```java
  if (countConnectedOrConnecting() >= MAX_CONNECTED_AUDIO_DEVICES) {
      // either reject, or evict the least-recently-active device:
      //   disconnect(mActiveDevice == device ? oldestOther() : mActiveDevice);
      return false;
  }
  A2dpSinkStateMachine sm = getOrCreateStateMachine(device);
  sm.sendMessage(A2dpSinkStateMachine.CONNECT, device);
  ```
* `disconnect(device)`: route to that device's SM; remove the SM from the map
  once it reaches `Disconnected`.
* `setActiveDevice(device)` (new): the switch action —
  1. `AvrcpControllerService.sendPassThroughCmd(mActiveDevice, PAUSE, PRESSED/RELEASED)`
     to stop the old device (patch 0001 already makes this device-correct);
  2. optionally `btif_a2dp_sink_set_rx_flush(true)` on the old stream to drop
     residual frames;
  3. `mActiveDevice = device`; grant audio focus to the new device's
     StreamHandler.
  All of this uses **existing** APIs — no native change is required for the
  switch itself.
* `informAvrcpPassThroughCmd(device, …)` / `informTGStatePlaying(device, …)`:
  route to `mStateMachines.get(device)` instead of a global SM.
* Binder methods: unchanged signatures; they already take a `BluetoothDevice`.

### 2.2 `A2dpSinkStateMachine.java`

* `make(A2dpSinkService svc, Context ctx, BluetoothDevice device)` — add the
  device parameter; store it as a single `final BluetoothDevice mDevice`.
* Delete `mCurrentDevice` / `mTargetDevice` / `mIncomingDevice` /
  `mPlayingDevice`; every reference becomes `mDevice` (the SM now only ever
  deals with its own device).  The `Connected.processMessage()` `CONNECT` case
  that disconnects a *different* current device is removed — a second device
  gets its **own** SM, so nothing is torn down.
* `getConnectedDevices()` inside the SM collapses to
  `isConnected() ? Collections.singletonList(mDevice) : emptyList()` (the
  aggregation now lives in the service).
* Keep `mAudioConfigs` as-is (already a per-device map; can become a single
  field now).
* The SM continues to own one `A2dpSinkStreamHandler`, constructed with
  `mDevice` (patch 0001 already changed the constructor to take the device).

### 2.3 `A2dpSinkStreamHandler.java` — **already done (patch 0001)**

It is bound to its device and sends AVRCP commands to `mDevice`, never to
`getConnectedDevices().get(0)`.  No further change is needed for F6 beyond what
patch 0001 provides; the per-device SM will hand each handler its own device.

### 2.4 Switching policy (plan section 16.6)

Three policies, all implementable in `setActiveDevice()`:

| Policy | Behaviour | Default |
|---|---|---|
| Preemptive | a new stream immediately takes over | ✅ recommended default |
| Polite | give the current device a 3–5 s grace period before switching | optional |
| Manual | user picks the active device in the UI | optional (UI lock) |

⛔ **Do not attempt simultaneous mixing of two streams.** The native sink
(`btif_a2dp_sink_cb`, the SBC/AAC decoder context, `rx_audio_queue`, the
AudioTrack handle, `rx_focus_state`) is a **global singleton**; making it
per-stream means rewriting the whole sink layer.  The MT6627 is also a
single-antenna radio sharing 2.4 GHz with Wi-Fi — two SBC streams already use
~60% of the air interface.  F6 is "two connected, one playing, fast switch",
not "two playing at once".

---

## 3. Native side (no change required for two links)

* `AVDT_NUM_LINKS = 2` (already the 8.1 default) supports two AVDTP streams.
* `BTA_AV_MAX_STREAMS = 2` is set in `device/keenplus/u2/bdroid_buildcfg.h`.
* For **one-to-n** (n > 2) you would additionally raise `AVDT_NUM_LINKS`,
  `AVDT_NUM_SEPS` and `AVDT_NUM_TC_TBL` — not needed for F6.
* On switch, the new stream triggers `btif_a2dp_sink_update_decoder(codec_info)`
  and `BtifAvrcpAudioTrackCreate()`, so two devices with different codecs /
  sample rates (e.g. A = SBC 44.1 kHz, B = AAC 48 kHz) rebuild the decoder and
  AudioTrack correctly.

---

## 4. Test plan (plan section 16.6) — run after the rewrite builds

| Case | Expected |
|---|---|
| A connected & playing → B initiates connection | B connects, **A stays connected** and keeps playing |
| A playing → B starts streaming | A receives AVRCP PAUSE and stops; B plays; switch < 1 s; no pop |
| B playing → A presses play | reverse switch works; 20 back-and-forth switches, no leak |
| A playing → B disconnects | A unaffected |
| Both press play (race) | deterministic behaviour (latecomer waits or is rejected), no deadlock, no double audio |
| A = SBC 44.1k, B = AAC 48k | after switch, sample rate / codec rebuilt correctly (`dumpsys media.audio_flinger`) |
| Screen off 10 min | both ACLs held; playback resumes |
| Soak | 200 switches, `dumpsys meminfo com.android.bluetooth` shows no leak |

---

## 5. Effort & risk

* **Effort:** 2–3 days once a build environment exists (plan section 22, 5d).
* **Risk R10 (plan section 20):** AVRCP Controller compatibility with some
  phones during switching — validate against 3 mainstream phones and keep the
  manual-switch fallback.
* **Validation gate:** this rewrite must be compiled (full AOSP build) and
  exercised on hardware against the matrix above before it can be considered
  done.  It is **not** covered by CI here, which can only prove that the shipped
  patch (0001) applies cleanly to `android-8.1.0_r80`.
