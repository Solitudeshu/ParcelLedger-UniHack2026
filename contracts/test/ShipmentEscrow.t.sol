// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {ShipmentEscrow} from "../src/ShipmentEscrow.sol";
import {EscrowFactory} from "../src/EscrowFactory.sol";

interface Vm {
    function addr(uint256 key) external returns (address);
    function sign(uint256 key, bytes32 digest) external returns (uint8 v, bytes32 r, bytes32 s);
    function prank(address sender) external;
    function startPrank(address sender) external;
    function stopPrank() external;
    function warp(uint256 time) external;
    function expectRevert(bytes4 selector) external;
    function expectRevert() external;
    function chainId(uint256 id) external;
}

contract TestToken is ERC20 {
    bool public fail;
    bool public fee;
    constructor() ERC20("Test", "TEST") {}
    function mint(address to, uint256 amount) external { _mint(to, amount); }
    function setFail(bool value) external { fail = value; }
    function setFee(bool value) external { fee = value; }
    function transfer(address to, uint256 amount) public override returns (bool) {
        require(!fail, "TRANSFER_FAILED");
        return super.transfer(to, amount);
    }
    function transferFrom(address from, address to, uint256 amount) public override returns (bool) {
        return super.transferFrom(from, to, fee ? amount - 1 : amount);
    }
}

/// @dev An ERC-1271 test double requiring two distinct EOA approvals, not a real Safe deployment.
contract QuorumMock {
    address[3] public signers;
    constructor(address a, address b, address c) { signers = [a, b, c]; }
    function isValidSignature(bytes32 digest, bytes memory signature) external view returns (bytes4) {
        (bytes memory first, bytes memory second) = abi.decode(signature, (bytes, bytes));
        address a = ECDSA.recover(digest, first);
        address b = ECDSA.recover(digest, second);
        bool memberA = a == signers[0] || a == signers[1] || a == signers[2];
        bool memberB = b == signers[0] || b == signers[1] || b == signers[2];
        return memberA && memberB && a != b ? bytes4(0x1626ba7e) : bytes4(0xffffffff);
    }
}

contract ShipmentEscrowTest {
    Vm constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));
    uint256 constant M = 0xA11CE;
    uint256 constant B = 0xB0B;
    uint256 constant A1 = 0xA1;
    uint256 constant A2 = 0xA2;
    uint256 constant A3 = 0xA3;
    uint256 constant AMOUNT = 100_000_000;
    address merchant;
    address buyer;
    TestToken token;
    QuorumMock arbitrator;
    EscrowFactory factory;
    ShipmentEscrow escrow;

    function setUp() public {
        vm.chainId(43113);
        vm.warp(1_800_000_000);
        merchant = vm.addr(M);
        buyer = vm.addr(B);
        token = new TestToken();
        arbitrator = new QuorumMock(vm.addr(A1), vm.addr(A2), vm.addr(A3));
        factory = new EscrowFactory(address(this));
        factory.setAllowedToken(address(token), true);
        factory.setAllowedArbitrator(address(arbitrator), true);
        escrow = create(bytes32(uint256(26)));
        token.mint(merchant, AMOUNT * 10);
        vm.prank(merchant);
        token.approve(address(escrow), type(uint256).max);
    }
    function create(bytes32 id) internal returns (ShipmentEscrow) {
        ShipmentEscrow.Terms memory t = ShipmentEscrow.Terms(id, merchant, buyer, address(token), AMOUNT,
            uint64(block.timestamp + 1 days), uint64(block.timestamp + 3 days), address(arbitrator), keccak256("terms"));
        vm.prank(merchant);
        return ShipmentEscrow(factory.createCase(t));
    }
    function sig(uint256 key, bytes32 digest) internal returns (bytes memory) {
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(key, digest);
        return abi.encodePacked(r, s, v);
    }
    function activate() internal {
        vm.prank(merchant); escrow.fund();
        vm.prank(merchant); escrow.commitDispatch(keccak256("dispatch"));
        uint64 expiry = uint64(block.timestamp + 1 hours);
        bytes32 digest = escrow.termsDigest(expiry);
        escrow.activate(expiry, sig(M, digest), sig(B, digest));
        vm.prank(buyer); escrow.commitReceipt(keccak256("receipt"));
    }
    function approve(uint256 version) internal {
        ShipmentEscrow.ReportApproval memory a = ShipmentEscrow.ReportApproval(
            keccak256(abi.encode("report", version)), escrow.evidenceRoot(), version, escrow.reportNonce(), uint64(block.timestamp + 1 hours));
        bytes32 digest = escrow.reportDigest(a);
        escrow.acceptReport(a, sig(M, digest), sig(B, digest));
    }
    function allocation(uint256 buyerAmount) internal view returns (ShipmentEscrow.Settlement memory) {
        return ShipmentEscrow.Settlement(escrow.acceptedReportHash(), escrow.evidenceRoot(), escrow.acceptedReportVersion(),
            AMOUNT - buyerAmount, buyerAmount, escrow.settlementNonce(), uint64(block.timestamp + 1 hours));
    }
    function settle(uint256 buyerAmount) internal {
        ShipmentEscrow.Settlement memory a = allocation(buyerAmount);
        bytes32 digest = escrow.settlementDigest(a);
        escrow.settle(a, sig(M, digest), sig(B, digest));
    }
    function conservation() internal view {
        require(escrow.totalWithdrawn() + escrow.claimable(merchant) + escrow.claimable(buyer) + escrow.unallocated() == escrow.totalFunded(), "CONSERVATION");
        require(token.balanceOf(address(escrow)) >= escrow.claimable(merchant) + escrow.claimable(buyer) + escrow.unallocated(), "SOLVENCY");
    }

    function testFuzzSettlementConservesFunds(uint256 buyerAmount) public {
        buyerAmount %= AMOUNT + 1;
        activate(); approve(1); settle(buyerAmount); conservation();
        if (buyerAmount > 0) { vm.prank(buyer); escrow.withdraw(); }
        if (buyerAmount < AMOUNT) { vm.prank(merchant); escrow.withdraw(); }
        conservation();
        require(escrow.totalWithdrawn() == AMOUNT, "WITHDRAWN");
        require(uint256(escrow.state()) == uint256(ShipmentEscrow.State.RESOLVED), "RESOLVED");
    }
    function testStaleReportAndReplayRejected() public {
        activate(); approve(1);
        ShipmentEscrow.Settlement memory a = allocation(25_000_000);
        bytes32 digest = escrow.settlementDigest(a);
        bytes memory m = sig(M, digest); bytes memory b = sig(B, digest);
        approve(2);
        vm.expectRevert(ShipmentEscrow.InvalidCommitment.selector); escrow.settle(a, m, b);
        a = allocation(25_000_000); digest = escrow.settlementDigest(a);
        m = sig(M, digest); b = sig(B, digest);
        escrow.settle(a, m, b);
        vm.expectRevert(ShipmentEscrow.InvalidState.selector); escrow.settle(a, m, b);
        conservation();
    }
    function testWrongSignerAmountChainAndContractRejected() public {
        activate(); approve(1);
        ShipmentEscrow.Settlement memory a = allocation(20_000_000);
        bytes32 digest = escrow.settlementDigest(a);
        bytes memory m = sig(M, digest); bytes memory b = sig(B, digest);
        bytes memory wrong = sig(A1, digest);
        vm.expectRevert(ShipmentEscrow.SignatureInvalid.selector); escrow.settle(a, m, wrong);
        a.buyerAmount += 1; a.merchantAmount -= 1;
        vm.expectRevert(ShipmentEscrow.SignatureInvalid.selector); escrow.settle(a, m, b);
        a.buyerAmount -= 1; a.merchantAmount += 1;
        vm.chainId(1);
        vm.expectRevert(ShipmentEscrow.SignatureInvalid.selector); escrow.settle(a, m, b);
        vm.chainId(43113);
        ShipmentEscrow first = escrow;
        escrow = create(bytes32(uint256(27)));
        vm.prank(merchant); token.approve(address(escrow), type(uint256).max);
        activate(); approve(1);
        // Same report/evidence can still not move signatures to a different case or contract.
        vm.expectRevert(); escrow.settle(a, m, b);
        escrow = first;
        conservation();
    }
    function testNewEvidenceInvalidatesReportAndPendingSignatures() public {
        activate(); approve(1);
        ShipmentEscrow.Settlement memory a = allocation(10_000_000);
        bytes32 digest = escrow.settlementDigest(a);
        bytes memory m = sig(M, digest); bytes memory b = sig(B, digest);
        vm.prank(buyer); escrow.appendEvidence(keccak256("extra-photo"));
        require(escrow.acceptedReportHash() == bytes32(0), "INVALIDATED");
        vm.expectRevert(ShipmentEscrow.InvalidCommitment.selector); escrow.settle(a, m, b);
        approve(2); settle(10_000_000); conservation();
    }
    function testExpiredAndWrongNonceRejected() public {
        activate(); approve(1);
        ShipmentEscrow.Settlement memory a = allocation(0);
        a.nonce += 1;
        bytes32 digest = escrow.settlementDigest(a); bytes memory m = sig(M, digest); bytes memory b = sig(B, digest);
        vm.expectRevert(ShipmentEscrow.NonceMismatch.selector); escrow.settle(a, m, b);
        a.nonce -= 1; a.expiresAt = uint64(block.timestamp - 1);
        digest = escrow.settlementDigest(a); m = sig(M, digest); b = sig(B, digest);
        vm.expectRevert(ShipmentEscrow.SignatureExpired.selector); escrow.settle(a, m, b);
    }
    function testDisputeRequiresTwoDistinctArbitratorSigners() public {
        activate();
        vm.prank(buyer); escrow.openDispute(keccak256("complaint"));
        vm.warp(block.timestamp + 4 days);
        vm.expectRevert(ShipmentEscrow.InvalidState.selector); escrow.closeExpiredUncontested();
        ShipmentEscrow.Resolution memory r = ShipmentEscrow.Resolution(escrow.evidenceRoot(), keccak256("decision"),
            40_000_000, 60_000_000, escrow.disputeNonce(), uint64(block.timestamp + 1 hours));
        bytes32 digest = escrow.resolutionDigest(r);
        bytes memory first = sig(A1, digest);
        bytes memory duplicate = abi.encode(first, first);
        vm.expectRevert(ShipmentEscrow.SignatureInvalid.selector); escrow.resolveDispute(r, duplicate);
        escrow.resolveDispute(r, abi.encode(first, sig(A2, digest)));
        require(escrow.claimable(buyer) == 60_000_000, "ARBITRATION_AMOUNT"); conservation();
    }
    function testPauseCannotBlockDisputeResolutionOrWithdraw() public {
        activate(); approve(1);
        factory.setIntakePaused(true);
        vm.prank(buyer); escrow.openDispute(keccak256("complaint"));
        settle(50_000_000);
        vm.prank(buyer); escrow.withdraw(); conservation();
    }
    function testTransferFailurePreservesWithdrawalRight() public {
        activate(); approve(1); settle(50_000_000);
        token.setFail(true);
        vm.prank(buyer); vm.expectRevert(); escrow.withdraw();
        require(escrow.claimable(buyer) == 50_000_000, "CLAIM_PRESERVED");
        require(escrow.totalWithdrawn() == 0, "NOT_WITHDRAWN");
        token.setFail(false); vm.prank(buyer); escrow.withdraw(); conservation();
    }
    function testFeeTokenRejectedWithoutLockingFunds() public {
        token.setFee(true);
        vm.prank(merchant); vm.expectRevert(ShipmentEscrow.UnsupportedTokenBehavior.selector); escrow.fund();
        require(escrow.totalFunded() == 0 && token.balanceOf(address(escrow)) == 0, "FUND_REVERTED");
        token.setFee(false); vm.prank(merchant); escrow.fund(); conservation();
    }
    function testCancelAfterActivationDeadline() public {
        vm.prank(merchant); escrow.fund();
        vm.expectRevert(ShipmentEscrow.DeadlineNotReached.selector); escrow.cancelUnactivated();
        vm.warp(block.timestamp + 1 days + 1);
        escrow.cancelUnactivated(); vm.prank(merchant); escrow.withdraw(); conservation();
    }
    function testDeadlineBoundaryAndCloseOnlyOnce() public {
        activate();
        vm.warp(block.timestamp + 3 days);
        vm.expectRevert(ShipmentEscrow.DeadlineNotReached.selector); escrow.closeExpiredUncontested();
        vm.warp(block.timestamp + 1);
        vm.prank(buyer); vm.expectRevert(ShipmentEscrow.DeadlinePassed.selector); escrow.openDispute(keccak256("late"));
        escrow.closeExpiredUncontested();
        vm.expectRevert(ShipmentEscrow.InvalidState.selector); escrow.closeExpiredUncontested();
        conservation();
    }
    function testDispatchImmutableAndPartyRoles() public {
        vm.prank(buyer); vm.expectRevert(ShipmentEscrow.Unauthorized.selector); escrow.commitDispatch(keccak256("fake"));
        vm.prank(merchant); escrow.commitDispatch(keccak256("first"));
        vm.prank(merchant); vm.expectRevert(ShipmentEscrow.InvalidCommitment.selector); escrow.commitDispatch(keccak256("replacement"));
    }
}
